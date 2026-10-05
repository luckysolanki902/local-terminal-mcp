"""Build the argv for a local image generator from a command template.

The operator opts in by configuring a command template (``--image-gen-cmd``)
that names a local generator and uses two placeholders:

- ``{prompt}`` — replaced with the prompt as a single argv token (spaces kept).
- ``{output}`` — replaced with the (root-contained) output file path.

Example templates::

    mflux-generate --model schnell --steps 2 --prompt {prompt} --output {output}
    sd -p {prompt} -o {output} --steps 8

The template is tokenised once with :mod:`shlex`; placeholders are substituted
per-token, so a prompt with spaces stays one argument and nothing is passed
through a shell.
"""

from __future__ import annotations

import shlex


class GeneratorConfigError(ValueError):
    """Raised when the image-generator command template is invalid."""


def build_generator_argv(template: str, prompt: str, output: str) -> list[str]:
    """Return the argv for ``template`` with placeholders substituted."""
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise GeneratorConfigError(
            f"could not parse image-gen command: {exc}"
        ) from None
    if not tokens:
        raise GeneratorConfigError("image-gen command is empty")
    if "{output}" not in template:
        raise GeneratorConfigError(
            "image-gen command must include the {output} placeholder"
        )

    argv = [
        token.replace("{prompt}", prompt).replace("{output}", output)
        for token in tokens
    ]
    return argv
