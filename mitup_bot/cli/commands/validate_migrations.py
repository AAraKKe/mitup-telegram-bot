from dataclasses import dataclass, field
from typing import Self

import click
from alembic.config import Config
from alembic.script import ScriptDirectory
from rich.console import Console, RenderableType
from rich.tree import Tree

console = Console(width=90)


@dataclass
class Revision:
    revision: str
    description: str
    child_revisions: list[Self] = field(default_factory=list)

    def __str__(self) -> str:
        color = "red" if len(self.child_revisions) > 1 else "green"
        return f"[bold {color}]{self.revision}[/] {self.description}"

    def renderable(self) -> str | RenderableType:
        guide_style = "bold red" if len(self.child_revisions) > 1 else "bold green"

        tree = Tree(str(self), guide_style=guide_style)
        for to in self.child_revisions:
            tree.add(str(to))
        return tree

    def __hash__(self) -> int:
        return hash(self.revision)


def build_migration_graph(config: Config) -> Revision:
    cache: dict[str | None, Revision] = {}

    directory = ScriptDirectory.from_config(config)

    for revision in directory.walk_revisions():
        rev_id = revision.revision

        if rev_obj := cache.get(rev_id):
            # In case the revision has been stored before without description
            rev_obj.description = revision.doc
        else:
            rev_obj = Revision(revision=rev_id, description=revision.doc)
            cache[rev_id] = rev_obj

        down_id = revision.down_revision
        if isinstance(down_id, str) or down_id is None:
            down_obj = cache.setdefault(down_id, Revision(revision=down_id or "Base", description=""))
            down_obj.child_revisions.append(rev_obj)
        else:
            raise RuntimeError(f"Down revision {down_id!r} is not allowed. Only strings are allowed.")

    return cache[None]


def validate_and_draw(base_revision: Revision) -> bool:
    if len(base_revision.child_revisions) == 0:
        return False

    found_branch = len(base_revision.child_revisions) > 1

    for revision in base_revision.child_revisions:
        console.print(revision.renderable())
        console.print()
        found_branch = found_branch or validate_and_draw(revision)

    return found_branch


@click.command()
@click.option(
    "-p", "--revisions-path", help="Path to the folder containing alembic revisions", show_default=True, default=None
)
def cli(revisions_path: str | None):
    if revisions_path is None:
        config = Config("alembic.ini")
    else:
        config = Config()
        config.set_main_option("script_location", revisions_path)

    try:
        revision_graph = build_migration_graph(config)
    except KeyError as exc:
        console.print(f"[bold red]Revision {exc.args[0]!r} is referenced by another revision but it doesn't exist")
        raise click.Abort() from exc

    console.rule("Migrations List")
    if validate_and_draw(revision_graph):
        console.print("[bold red]Branching migrations found! Check the output above.")
        raise click.Abort()
