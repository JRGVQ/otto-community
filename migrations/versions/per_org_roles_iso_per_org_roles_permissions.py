"""Per-org roles & permissions (tenant isolation of RBAC)

Roles used to be global (one shared set across every tenant), so a company admin
editing the permission matrix changed it for ALL companies. This makes roles
per-organisation: each org gets its own role set and its own role x permission
matrix, fully isolated from other tenants. The Permission *catalogue* stays global.

Each organisation is rebuilt from the canonical ROLE_MATRIX (a clean slate that heals
any earlier tampering of the shared matrix); users are re-pointed to the same-named
role in their own org. The orphaned global roles are then removed.

Revision ID: per_org_roles_iso
Revises: e1f2a3b4c5d6
Create Date: 2026-06-29

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "per_org_roles_iso"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # 1. Add organization_id (nullable for now) and drop the global UNIQUE(name) so
    #    every org can have its own role with the same name (e.g. "Eigenaar").
    with op.batch_alter_table("roles", schema=None) as batch:
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch.drop_constraint("uq_roles_name", type_="unique")

    meta = sa.MetaData()
    roles = sa.Table("roles", meta, autoload_with=bind)
    rps = sa.Table("role_permissions", meta, autoload_with=bind)
    perms = sa.Table("permissions", meta, autoload_with=bind)
    users = sa.Table("users", meta, autoload_with=bind)
    orgs = sa.Table("organizations", meta, autoload_with=bind)

    # The canonical role/permission policy (source of truth) — every org is rebuilt
    # from this clean slate, so any earlier tampering of the shared matrix is healed.
    from app.constants import PERMISSIONS, ROLE_MATRIX, ROLES

    # Permission catalogue (global). Insert any missing keys so the matrix is complete.
    perm_id_by_key = {key: pid for pid, key in bind.execute(sa.select(perms.c.id, perms.c.key))}
    for key, label in PERMISSIONS:
        if key not in perm_id_by_key:
            new_pid = bind.execute(perms.insert().values(key=key, label=label)).inserted_primary_key[0]
            perm_id_by_key[key] = new_pid

    # 2. Build each organisation's OWN role set from the canonical matrix, then
    #    re-point that org's users (matched by their old global role's name).
    global_roles = list(
        bind.execute(sa.select(roles.c.id, roles.c.name).where(roles.c.organization_id.is_(None)))
    )
    name_by_global_id = {gid: name for gid, name in global_roles}
    org_ids = [row[0] for row in bind.execute(sa.select(orgs.c.id))]

    for org_id in org_ids:
        new_id_by_name = {}
        for name in ROLES:
            new_id = bind.execute(
                roles.insert().values(organization_id=org_id, name=name, is_owner=(name == "Eigenaar"))
            ).inserted_primary_key[0]
            new_id_by_name[name] = new_id
            for key, _label in PERMISSIONS:
                bind.execute(
                    rps.insert().values(
                        role_id=new_id, permission_id=perm_id_by_key[key], allowed=bool(ROLE_MATRIX[name][key])
                    )
                )
        for gid, name in name_by_global_id.items():
            if name in new_id_by_name:
                bind.execute(
                    users.update()
                    .where(sa.and_(users.c.organization_id == org_id, users.c.role_id == gid))
                    .values(role_id=new_id_by_name[name])
                )

    # 3. Remove the now-orphaned global roles (+ their permission cells).
    global_ids = [gid for gid, _name in global_roles]
    if global_ids:
        bind.execute(rps.delete().where(rps.c.role_id.in_(global_ids)))
        bind.execute(roles.delete().where(roles.c.id.in_(global_ids)))

    # 4. Lock the schema down: NOT NULL + FK + per-org UNIQUE(name).
    with op.batch_alter_table("roles", schema=None) as batch:
        batch.alter_column("organization_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_roles_organization_id_organizations", "organizations", ["organization_id"], ["id"]
        )
        batch.create_unique_constraint("uq_role_org_name", ["organization_id", "name"])


def downgrade():
    """Collapse per-org roles back to a single global set (keeps the lowest-id role
    per name, re-points users, drops the rest). Lossy for any per-org customisation."""
    bind = op.get_bind()

    with op.batch_alter_table("roles", schema=None) as batch:
        batch.drop_constraint("uq_role_org_name", type_="unique")
        batch.drop_constraint("fk_roles_organization_id_organizations", type_="foreignkey")

    meta = sa.MetaData()
    roles = sa.Table("roles", meta, autoload_with=bind)
    rps = sa.Table("role_permissions", meta, autoload_with=bind)
    users = sa.Table("users", meta, autoload_with=bind)

    canonical = {}
    for rid, name in bind.execute(sa.select(roles.c.id, roles.c.name).order_by(roles.c.id)):
        canonical.setdefault(name, rid)
    for rid, name in list(bind.execute(sa.select(roles.c.id, roles.c.name))):
        keep = canonical[name]
        if rid != keep:
            bind.execute(users.update().where(users.c.role_id == rid).values(role_id=keep))
            bind.execute(rps.delete().where(rps.c.role_id == rid))
            bind.execute(roles.delete().where(roles.c.id == rid))

    with op.batch_alter_table("roles", schema=None) as batch:
        batch.drop_column("organization_id")
        batch.create_unique_constraint("uq_roles_name", ["name"])
