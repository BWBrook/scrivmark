# scrivmark

> **Status:** developed in the open as a working record. This is work in progress: contents may change, and nothing here is peer-reviewed unless stated.

Manuscript review on Markdown and Quarto sources, without Word. A reviewer's suggested edits are real edits on a review branch, shown as redline against the author's version; comments are threads in a review record beside the text, anchored to the source. A local viewer renders the manuscript with redline and margin threads beside a Claude Code or Codex chat, and the agent works on the same review through a CLI. The author resolves the review in the same viewer, then merges.

Nothing is built yet. The design is in [`docs/DESIGN.md`](docs/DESIGN.md), with its independent review in [`docs/audits/`](docs/audits/).

## Getting started

Not yet installable. When it is: `uv tool install git+https://github.com/BWBrook/scrivmark`.

## How this work is done

Developed by Barry W. Brook with AI collaborators. Claude (Opus 5.5, in Claude Code) surveyed existing tools, ran the design interview with Barry, wrote the design and adjudicated its review. Codex (`gpt-6.1-sol`) carried out the independent adversarial design review.

## Citation

See [`CITATION.cff`](CITATION.cff). Releases of paper-grade milestones carry a
Zenodo DOI.

## Licence

Code is released under the [MIT Licence](LICENSE). Text, figures and data created for this project are released under [CC BY-SA 4.0](LICENSE-CONTENT). Third-party material keeps its own terms where stated.
