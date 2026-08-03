I work on the metric backend of [Checkmk](https://checkmk.com), the new data backend for OpenTelemetry metrics that lets Checkmk monitor applications, not just infrastructure.\
Before that I was at SAP, building the CI/CD infrastructure for HANA (their in-memory database): a graph-based task execution framework running on ~2000 compute nodes.\
That's where I learned that at sufficient scale everything fails all the time, and the only real feature is being able to watch it happen.

Python and TypeScript at work, Rust to relax, and a 🦆 for the hard parts.

#### Things I made

- [**csshw**](https://github.com/whme/csshw): a cluster SSH tool for Windows, one window that broadcasts your keystrokes to any number of SSH sessions.\
  A cross-platform successor is in the works ([cssh-rs](https://github.com/whmade/cssh-rs)).
- [**Rustifying Python**](https://whme.github.io/PyConDE-2025/Rustifying_Python_PyConDE25_Max_Hoehl.pdf): my PyConDE 2025 talk about figuring out which parts of a Python codebase are worth moving to Rust, why, and what will go wrong when you do.\
  Based on my team doing exactly that at SAP.

#### Currently working on

<!-- activity:start -->
- <img src="https://github.com/Checkmk.png?size=32" width="16" height="16" alt=""> [**Checkmk/checkmk**](https://github.com/Checkmk/checkmk)
  - <img src="assets/git-commit.svg" width="16" height="16" alt="commit"> [metric-backend: make dropdowns with an empty state clearable](https://github.com/Checkmk/checkmk/commit/31fe0c7e22e8d530652fca198675c2f87c23e79c)
- <img src="https://github.com/Checkmk.png?size=32" width="16" height="16" alt=""> [**Checkmk/otter**](https://github.com/Checkmk/otter)
  - <img src="assets/git-pull-request.svg" width="16" height="16" alt="pr"> [Add `dispatch` trigger for workflow-to-workflow handoff](https://github.com/Checkmk/otter/pull/1)
- <img src="https://github.com/whmade.png?size=32" width="16" height="16" alt=""> [**whmade/cssh-rs**](https://github.com/whmade/cssh-rs)
  - <img src="assets/git-commit.svg" width="16" height="16" alt="commit"> [automation: pin Git bash for ScriptedShellMode forced command (#262)](https://github.com/whmade/cssh-rs/commit/70f73e63021f974c67608b2e1e3ba8cde350b802)
- <img src="https://github.com/whme.png?size=32" width="16" height="16" alt=""> [**whme/csshw**](https://github.com/whme/csshw)
  - <img src="assets/git-pull-request.svg" width="16" height="16" alt="pr"> [Delete .github/dependabot.yml](https://github.com/whme/csshw/pull/270)
<!-- activity:end -->

<sub>The list above refreshes daily via a [small workflow](.github/workflows/update-readme.yml).\
The 🦆 is maintained by hand.</sub>
