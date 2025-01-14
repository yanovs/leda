from __future__ import annotations

import dataclasses
import datetime
import logging
import os
import pathlib
from typing import Any

import jupyter_client.kernelspec
import nbconvert
from nbconvert import preprocessors
import nbformat
import packaging.version
import termcolor
import tqdm
import traitlets
from typing_extensions import override

import leda.gen.base

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ExecutePreprocessorWithProgressBar(preprocessors.ExecutePreprocessor):
    """Small extension to provide progress bar."""

    progress = traitlets.Bool(
        default_value=False  # pyright: ignore[reportGeneralTypeIssues]
    ).tag(config=True)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Progress bar state
        self._num_cells: int | None = None
        self._pbar: tqdm.tqdm | None = None

    @override
    def preprocess(
        self,
        nb: nbformat.NotebookNode,
        resources: dict | None = None,
        km: jupyter_client.KernelManager | None = None,
    ) -> tuple[nbformat.NotebookNode, dict]:
        self._num_cells = len(nb["cells"])

        result = super().preprocess(nb, resources, km=km)
        if self._pbar is not None:
            self._pbar.close()

        return result

    @override
    def preprocess_cell(
        self,
        cell: nbformat.NotebookNode,
        resources: dict,
        index: int,
        store_history: bool = True,
    ) -> tuple[nbformat.NotebookNode, dict]:
        if self._pbar is None:
            self._pbar = tqdm.tqdm(
                desc="Executing notebook",
                total=self._num_cells,
                disable=not self.progress,
            )

        cell_lines = cell.source.splitlines()
        if cell_lines:
            first_line = termcolor.colored(cell_lines[0], color="green")
        else:
            first_line = ""
        self._pbar.set_postfix_str(first_line)

        # Note that preprocess_cell() will actually run the cell
        result = super().preprocess_cell(cell, resources, index)
        self._pbar.update(1)

        return result  # type: ignore[no-any-return]


@dataclasses.dataclass()
class MainStaticReportGenerator(leda.gen.base.ReportGenerator):
    cell_timeout: datetime.timedelta | None = None
    kernel_name: str | None = None
    progress: bool = False

    template_name: str | None = None
    theme: str | None = None

    def __post_init__(self) -> None:
        nbconvert_version = packaging.version.parse(nbconvert.__version__)
        is_classic = self.template_name == "classic" or (
            not self.template_name and nbconvert_version.major < 6
        )
        if is_classic and self.theme == "dark":
            raise ValueError(
                f"Unsupported theme in 'classic' template: {self.theme!r}"
            )

        if self.theme not in (None, "light", "dark"):
            raise ValueError(f"Unsupported theme: {self.theme!r}")

    def _get_preprocessor(
        self,
    ) -> preprocessors.ExecutePreprocessor:
        kwargs: dict[str, Any] = {}

        if self.cell_timeout:
            kwargs["timeout"] = int(self.cell_timeout.total_seconds())

        if self.kernel_name:
            kernel_specs = jupyter_client.kernelspec.find_kernel_specs()
            if self.kernel_name in kernel_specs:
                kwargs["kernel_name"] = self.kernel_name
            else:
                raise ValueError(
                    f"The kernel {self.kernel_name!r} could not be found. "
                    f"Kernel choices: {kernel_specs.keys()}"
                )

        return ExecutePreprocessorWithProgressBar(
            progress=self.progress, **kwargs
        )

    def _get_exporter_kwargs(self) -> dict:
        # See https://nbconvert.readthedocs.io/en/latest/customizing.html#adding-additional-template-paths  # noqa
        exporter_kwargs = {
            "extra_template_basedirs": str(
                pathlib.Path(__file__).parent.parent / "templates"
            )
        }

        if self.template_name:
            exporter_kwargs["template_name"] = self.template_name

        if self.theme:
            exporter_kwargs["theme"] = self.theme

        return exporter_kwargs

    @override
    def generate(
        self,
        nb_contents: nbformat.NotebookNode,
        html_title: str | None = None,
    ) -> bytes:
        logger.info("Generating notebook")
        preprocessor = self._get_preprocessor()
        preprocessor.preprocess(
            nb_contents, resources={"metadata": {"path": os.getcwd()}}
        )

        logger.info("Generating HTML")
        exporter = nbconvert.HTMLExporter(**self._get_exporter_kwargs())
        body: str
        body, _ = exporter.from_notebook_node(nb_contents)

        logger.info("Modifying HTML")
        if html_title:
            body = body.replace(
                "<title>Notebook</title>", f"<title>{html_title}</title>"
            )

        return body.encode(errors="ignore")
