"""
OpenWhisk Compatible Packages API Endpoint.

RESTful API for managing packages in OpenWhisk namespaces.

Endpoints:
- GET /api/v1/namespaces/{namespace}/packages
  List all packages in namespace (with pagination and filtering)

- GET /api/v1/namespaces/{namespace}/packages/{packageName}
  Get package details with actions list

- PUT /api/v1/namespaces/{namespace}/packages/{packageName}
  Create or update package with binding support

- DELETE /api/v1/namespaces/{namespace}/packages/{packageName}
  Delete package (force=true deletes with contents)

Package Features:
- Package bindings to reference other packages
- Parameter inheritance from package to actions
- Publish/share packages across namespaces
- Annotations for metadata
- Version tracking
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from datetime import datetime

from flask import Blueprint, request, jsonify, g
from werkzeug.exceptions import BadRequest, NotFound, Conflict, Forbidden
import json

if TYPE_CHECKING:
    from pydal import DAL, Table

# Create Blueprint
packages_bp = Blueprint('packages', __name__, url_prefix='/api/v1')


# Helper Functions


def _get_pydal_db() -> DAL:
    """Get PyDAL database connection from Flask context."""
    from app.models import get_db
    if not hasattr(g, 'pydal_db') or g.pydal_db is None:
        g.pydal_db = get_db()
    return g.pydal_db


def _get_namespace_by_name(db: DAL, namespace_name: str) -> Optional[int]:
    """
    Get namespace ID by name.

    Args:
        db: PyDAL database instance
        namespace_name: Name of namespace

    Returns:
        Namespace ID if found, None otherwise
    """
    namespace = db(db.namespace.name == namespace_name).select().first()
    return namespace.id if namespace else None


def _get_package_by_name(
    db: DAL, namespace_id: int, package_name: str
) -> Optional[Dict[str, Any]]:
    """
    Get package record by name within a namespace.

    Args:
        db: PyDAL database instance
        namespace_id: ID of namespace
        package_name: Name of package

    Returns:
        Package record dict if found, None otherwise
    """
    package = db(
        (db.package.namespace_id == namespace_id) &
        (db.package.name == package_name)
    ).select().first()

    if not package:
        return None

    return _format_package_response(db, package)


def _get_actions_for_package(
    db: DAL, package_id: int
) -> List[Dict[str, Any]]:
    """
    Get all actions in a package.

    Args:
        db: PyDAL database instance
        package_id: ID of package

    Returns:
        List of action records
    """
    actions = db(db.action.package_id == package_id).select()
    return [
        {
            'name': action.name,
            'version': action.version,
            'publish': action.publish,
            'exec': {
                'kind': action.exec_kind,
                'code': action.exec_code if not action.exec_binary else None,
                'binary': action.exec_binary,
                'main': action.exec_main,
            },
            'annotations': action.annotations or {},
            'parameters': action.parameters or {},
            'limits': action.limits or {},
            'updated': int(action.updated_at.timestamp() * 1000),
        }
        for action in actions
    ]


def _resolve_package_binding(
    db: DAL, binding: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """
    Resolve a package binding to its target package.

    Args:
        db: PyDAL database instance
        binding: Binding dict with 'namespace' and 'name' keys

    Returns:
        Target package record if found, None otherwise
    """
    if not binding or 'namespace' not in binding or 'name' not in binding:
        return None

    target_ns = _get_namespace_by_name(db, binding['namespace'])
    if not target_ns:
        return None

    return _get_package_by_name(db, target_ns, binding['name'])


def _merge_parameters(
    package_params: Dict[str, Any],
    action_params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge package and action parameters (action overrides package).

    Args:
        package_params: Package-level parameters
        action_params: Action-level parameters

    Returns:
        Merged parameters dict
    """
    merged = (package_params or {}).copy()
    merged.update(action_params or {})
    return merged


def _format_package_response(
    db: DAL, package_record: Any
) -> Dict[str, Any]:
    """
    Format a package record for API response.

    Includes parameter inheritance and binding resolution.

    Args:
        db: PyDAL database instance
        package_record: PyDAL package record

    Returns:
        Formatted package dict for API response
    """
    package_dict = {
        'name': package_record.name,
        'namespace': db(db.namespace.id == package_record.namespace_id
                      ).select().first().name,
        'version': package_record.version or '0.0.1',
        'publish': package_record.publish,
        'annotations': package_record.annotations or {},
        'parameters': package_record.parameters or {},
        'updated': int(package_record.updated_at.timestamp() * 1000),
        'created': int(package_record.created_at.timestamp() * 1000),
    }

    # Handle package bindings
    if package_record.binding:
        binding = package_record.binding
        if isinstance(binding, str):
            binding = json.loads(binding)

        target_package = _resolve_package_binding(db, binding)
        if target_package:
            package_dict['binding'] = binding
            # Inherit target package parameters if not overridden
            target_params = (target_package.get('parameters') or {}).copy()
            target_params.update(package_dict.get('parameters') or {})
            package_dict['parameters'] = target_params

    return package_dict


def _validate_package_name(name: str) -> bool:
    """Validate package name format."""
    if not name or not isinstance(name, str):
        return False
    if len(name) > 256:
        return False
    # Allow alphanumeric, hyphens, underscores
    import re
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))


# Route Handlers


@packages_bp.route(
    '/namespaces/<namespace>/packages',
    methods=['GET']
)
def list_packages(namespace: str) -> Tuple[Dict[str, Any], int]:
    """
    List all packages in a namespace.

    Query Parameters:
    - limit: Maximum packages to return (default: 200, max: 1000)
    - skip: Number of packages to skip (default: 0)
    - public: Filter to shared packages only (default: false)

    Args:
        namespace: Namespace name

    Returns:
        JSON response with list of packages
    """
    try:
        db = _get_pydal_db()

        # Get namespace
        namespace_id = _get_namespace_by_name(db, namespace)
        if not namespace_id:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Parse pagination parameters
        limit = request.args.get('limit', 200, type=int)
        skip = request.args.get('skip', 0, type=int)
        public_only = request.args.get('public', 'false').lower() == 'true'

        # Validate parameters
        if limit > 1000:
            limit = 1000
        if limit < 1:
            limit = 1
        if skip < 0:
            skip = 0

        # Build query
        query = db.package.namespace_id == namespace_id

        # Filter public packages if requested
        if public_only:
            query = query & (db.package.publish == True)

        # Get total count
        total = db(query).count()

        # Get packages with pagination
        packages = db(query).select(
            limitby=(skip, skip + limit),
            orderby=db.package.updated_at
        )

        # Format response
        package_list = [_format_package_response(db, p) for p in packages]

        return jsonify({
            'packages': package_list,
            'total': total,
            'limit': limit,
            'skip': skip
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@packages_bp.route(
    '/namespaces/<namespace>/packages/<package_name>',
    methods=['GET']
)
def get_package(namespace: str, package_name: str) -> Tuple[Dict[str, Any], int]:
    """
    Get package details with actions list.

    Args:
        namespace: Namespace name
        package_name: Package name

    Returns:
        JSON response with package details
    """
    try:
        db = _get_pydal_db()

        # Get namespace
        namespace_id = _get_namespace_by_name(db, namespace)
        if not namespace_id:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Get package
        package = db(
            (db.package.namespace_id == namespace_id) &
            (db.package.name == package_name)
        ).select().first()

        if not package:
            return jsonify({
                'error': f'Package {package_name} not found in namespace {namespace}'
            }), 404

        # Format response with actions
        response = _format_package_response(db, package)
        response['actions'] = _get_actions_for_package(db, package.id)

        return jsonify(response), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@packages_bp.route(
    '/namespaces/<namespace>/packages/<package_name>',
    methods=['PUT']
)
def create_or_update_package(
    namespace: str, package_name: str
) -> Tuple[Dict[str, Any], int]:
    """
    Create or update a package.

    Query Parameters:
    - overwrite: Allow update if package exists (default: false)

    Request Body:
    {
        "name": "package_name",
        "version": "1.0.0",
        "publish": true,
        "parameters": {...},
        "annotations": {...},
        "binding": {"namespace": "ns", "name": "pkg"}
    }

    Args:
        namespace: Namespace name
        package_name: Package name

    Returns:
        JSON response with created/updated package
    """
    try:
        db = _get_pydal_db()
        overwrite = request.args.get('overwrite', 'false').lower() == 'true'

        # Validate package name
        if not _validate_package_name(package_name):
            return jsonify({
                'error': 'Invalid package name format'
            }), 400

        # Get namespace
        namespace_id = _get_namespace_by_name(db, namespace)
        if not namespace_id:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Get request body
        body = request.get_json() or {}

        # Check if package exists
        existing = db(
            (db.package.namespace_id == namespace_id) &
            (db.package.name == package_name)
        ).select().first()

        if existing and not overwrite:
            return jsonify({
                'error': f'Package {package_name} already exists. Use ?overwrite=true'
            }), 409

        # Prepare package data
        package_data = {
            'namespace_id': namespace_id,
            'name': package_name,
            'version': body.get('version', '0.0.1'),
            'publish': body.get('publish', False),
            'description': body.get('description', ''),
            'parameters': body.get('parameters', {}),
            'annotations': body.get('annotations', {}),
        }

        # Handle binding
        if 'binding' in body:
            binding = body['binding']
            if not isinstance(binding, dict) or \
               'namespace' not in binding or 'name' not in binding:
                return jsonify({
                    'error': 'Invalid binding format. Expected: '
                             '{"namespace": "ns", "name": "pkg"}'
                }), 400

            # Validate binding target exists
            target_ns = _get_namespace_by_name(db, binding['namespace'])
            if not target_ns:
                return jsonify({
                    'error': f'Binding namespace {binding["namespace"]} not found'
                }), 404

            target_pkg = db(
                (db.package.namespace_id == target_ns) &
                (db.package.name == binding['name'])
            ).select().first()
            if not target_pkg:
                return jsonify({
                    'error': f'Binding package {binding["name"]} not found'
                }), 404

            package_data['binding'] = json.dumps(binding)

        # Create or update
        if existing:
            existing_id = existing.id
            db.package[existing_id] = package_data
            db.commit()
        else:
            package_data['created_at'] = datetime.utcnow()
            package_data['updated_at'] = datetime.utcnow()
            existing_id = db.package.insert(**package_data)
            db.commit()

        # Get and return updated package
        updated = db.package[existing_id]
        response = _format_package_response(db, updated)

        status_code = 200 if existing else 201
        return jsonify(response), status_code

    except BadRequest as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@packages_bp.route(
    '/namespaces/<namespace>/packages/<package_name>',
    methods=['DELETE']
)
def delete_package(
    namespace: str, package_name: str
) -> Tuple[Dict[str, Any], int]:
    """
    Delete a package.

    Query Parameters:
    - force: Delete package with all contained actions (default: false)

    Args:
        namespace: Namespace name
        package_name: Package name

    Returns:
        JSON response confirming deletion
    """
    try:
        db = _get_pydal_db()
        force = request.args.get('force', 'false').lower() == 'true'

        # Get namespace
        namespace_id = _get_namespace_by_name(db, namespace)
        if not namespace_id:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Get package
        package = db(
            (db.package.namespace_id == namespace_id) &
            (db.package.name == package_name)
        ).select().first()

        if not package:
            return jsonify({
                'error': f'Package {package_name} not found in namespace {namespace}'
            }), 404

        # Check if package has actions
        actions_count = db(db.action.package_id == package.id).count()
        if actions_count > 0 and not force:
            return jsonify({
                'error': f'Package contains {actions_count} action(s). '
                         'Use ?force=true to delete with contents'
            }), 409

        # Delete package (cascade handles actions if they're FK-dependent)
        db(db.package.id == package.id).delete()
        db.commit()

        return jsonify({
            'namespace': namespace,
            'name': package_name,
            'deleted': True
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
