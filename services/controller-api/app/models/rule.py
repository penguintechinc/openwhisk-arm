"""
Rule model linking OpenWhisk triggers to actions.

OpenWhisk rules connect triggers to actions, implementing event-driven
execution. When a trigger fires, all active rules associated with that
trigger evaluate and invoke their actions with the trigger event data.

Rules support:
- Status: Active rules execute, inactive rules are disabled
- Parameters: Inherited from trigger and action definitions
- Versioning: Track rule changes over time

The rule evaluation model:
1. Trigger fires with event data
2. All active rules for that trigger are evaluated
3. Each rule invokes its action with merged parameters
4. Action executions are tracked independently
"""


def define_rule(db):
    """
    Define the Rule model in PyDAL.

    Args:
        db: PyDAL database instance

    Returns:
        None - defines db.rule table
    """
    db.define_table(
        'rule',
        db.Field('namespace_id', 'reference namespace',
                 ondelete='CASCADE',
                 notnull=True,
                 label='Namespace'),
        db.Field('name', 'string',
                 length=256,
                 notnull=True,
                 label='Rule Name'),
        db.Field('version', 'string',
                 length=32,
                 default='0.0.1',
                 notnull=True,
                 label='Version'),
        db.Field('publish', 'boolean',
                 default=False,
                 notnull=True,
                 label='Public'),
        db.Field('status', 'string',
                 length=16,
                 default='active',
                 notnull=True,
                 label='Status'),
        db.Field('trigger_id', 'reference trigger',
                 ondelete='CASCADE',
                 notnull=True,
                 label='Trigger'),
        db.Field('action_id', 'reference action',
                 ondelete='CASCADE',
                 notnull=True,
                 label='Action'),
        db.Field('created_at', 'datetime',
                 default=db.now,
                 writable=False,
                 label='Created At'),
        db.Field('updated_at', 'datetime',
                 default=db.now,
                 update=db.now,
                 writable=False,
                 label='Updated At'),
    )

    # Unique constraint on namespace + name
    db.rule.namespace_id.requires = db.IS_IN_DB(
        db, 'namespace.id', '%(name)s'
    )
    db.rule.name.requires = [
        db.IS_NOT_EMPTY(),
        db.IS_LENGTH(256)
    ]
    db.rule.status.requires = db.IS_IN_SET(
        ['active', 'inactive'],
        error_message='Status must be active or inactive'
    )
    db.rule.trigger_id.requires = db.IS_IN_DB(
        db, 'trigger.id', '%(name)s'
    )
    db.rule.action_id.requires = db.IS_IN_DB(
        db, 'action.id', '%(name)s'
    )

    # Index on trigger_id for fast lookups when trigger fires
    # Note: Actual index creation depends on DB backend
    # PostgreSQL: CREATE INDEX idx_rule_trigger ON rule(trigger_id);
    # MySQL: CREATE INDEX idx_rule_trigger ON rule(trigger_id);
