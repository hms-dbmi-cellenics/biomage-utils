import click

from biomage.configure_repo import configure_repo
from biomage.experiment import experiment
from biomage.release import release
from biomage.rotate_ci import rotate_ci
from biomage.stage import stage
from biomage.unstage import unstage


@click.group()
def main():
    """🧬 Your one-stop shop for managing Biomage infrastructure."""


main.add_command(configure_repo.configure_repo)
main.add_command(rotate_ci.rotate_ci)
main.add_command(stage.stage)
main.add_command(unstage.unstage)
main.add_command(experiment.experiment)
main.add_command(release.release)

if __name__ == "__main__":
    main()
