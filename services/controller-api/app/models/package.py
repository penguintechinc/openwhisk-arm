"""
OpenWhisk Package Model.

This module defines the Package model for grouping and organizing actions.
Packages provide namespacing and parameter inheritance for actions.

Package Semantics:
    - Packages group related actions together
    - Packages can define default parameters inherited by all actions
    - Packages can be published for sharing across namespaces
    - Packages support bindings to reference other packages

Package Bindings:
    A package binding creates a reference to another package, allowing:
    - Cross-namespace package sharing
    - Parameter overrides at binding level
    - Centralized package management with distributed access

    Binding format: {"namespace": "source_namespace", "name": "source_package"}

Parameter Inheritance:
    Parameters flow from package to actions:
    1. Package defines default parameters
    2. Actions inherit package parameters
    3. Action-level parameters override package defaults
    4. Binding parameters override source package parameters
"""

from pydal import Field


def define_package_table(db):
    """
    Define the Package table in PyDAL.

    Args:
        db: PyDAL database instance

    Returns:
        The defined package table
    """
    db.define_table(
        'package',
        Field('namespace_id', 'reference namespace', ondelete='CASCADE',
              notnull=True,
              comment='Namespace this package belongs to'),
        Field('name', 'string', length=256, notnull=True,
              comment='Package name (max 256 characters)'),
        Field('version', 'string', default='0.0.1',
              comment='Package version (semantic versioning)'),
        Field('publish', 'boolean', default=False,
              comment='Whether package is published for sharing'),
        Field('description', 'text',
              comment='Package description'),
        Field('parameters', 'json', default={},
              comment='Default parameters inherited by all actions in package'),
        Field('annotations', 'json', default={},
              comment='Package metadata and annotations'),
        Field('binding', 'json',
              comment='Package binding reference: {namespace, name}'),
        Field('created_at', 'datetime', default=lambda: db.now(),
              readable=True, writable=False,
              comment='Creation timestamp'),
        Field('updated_at', 'datetime', update=lambda: db.now(),
              default=lambda: db.now(),
              comment='Last update timestamp'),
        migrate=True,
        fake_migrate=False
    )

    # Unique constraint on (namespace_id, name)
    # Package names must be unique within a namespace
    db.executesql(
        '''CREATE UNIQUE INDEX IF NOT EXISTS idx_package_namespace_name
           ON package(namespace_id, name)'''
    )

    # Index on namespace_id for fast lookups
    db.executesql(
        '''CREATE INDEX IF NOT EXISTS idx_package_namespace
           ON package(namespace_id)'''
    )

    return db.package
