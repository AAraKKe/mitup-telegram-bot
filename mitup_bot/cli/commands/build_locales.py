import subprocess

import click

from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine


@click.command()
@click.pass_context
def cli(ctx: click.Context):
    """Generate translations for the bot."""
    for lang in SUPPORTED_LANGUAGES:
        po_path = TranslationEngine.LOCALES_DIR / f"{lang}/LC_MESSAGES/{TranslationEngine.DOMAIN}.po"
        mo_path = po_path.with_suffix(".mo")

        if not po_path.exists():
            print(f"ERROR: Po file {po_path} does not exist.")
            ctx.exit(1)

        # Compile po file
        print(f"Compiling {lang!r} po file...")

        try:
            subprocess.run(["msgfmt", "-o", mo_path.absolute(), po_path.absolute()], check=True)
            print(f"Successfully compiled {po_path} to {mo_path}")
        except subprocess.CalledProcessError as e:
            print(f"Error compiling {po_path}: {e}")
            ctx.exit(1)
