import click

from .migrator import migrator
from .run import run
from .token import token
from .tunnel import tunnel


@click.group()
def rds():
    """
    Manage Cellenics RDS databases.
    """
    pass


rds.add_command(tunnel)
rds.add_command(run)
rds.add_command(token)
rds.add_command(migrator)
