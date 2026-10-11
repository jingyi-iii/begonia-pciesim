# begonia-pciesim

One PCIe design, simulated at two levels of abstraction.  `rtl-sim` runs LitePCIe's real TLP packetizer under Verilator with cocotb, cycle accurate, no operating system.  `qemu-sim` compiles a PCIe endpoint device model into QEMU and drives it with a qtest suite and with a real Alpine Linux guest.

## Layout

| Path | Purpose |
| --- | --- |
| `rtl-sim/` | Verilator + cocotb simulation of LitePCIe's TLP packetizer |
| `rtl-sim/README.md` | What it is and how to run it |
| `rtl-sim/DESIGN.md` | The `gen_rtl` traps, timing convention, parameter matrix, environment |
| `qemu-sim/` | QEMU device model plus real-guest enumeration |
| `qemu-sim/README.md` | What it is and how to run it |
| `qemu-sim/NOTES.md` | The QEMU 11 API changes, register map, overrides, troubleshooting |

## Language conventions

- Conversation with the user: Chinese.
- All generated artifacts and files (including this `AGENTS.md`): English.

## README style

`README.md` follows the house style of the other `jingyi-iii` repositories
(`begonia-os`, `tangerine-github-access`, `tangerine-nudge`, `tangerine-screenshot`):

- the title is `# <repo-name>` alone, with no tagline on the same line;
- two or three short prose paragraphs open it: what it is, what works today, what is unusual
  about it. No bullet list and no bold `**Status:**` line up front;
- one table follows, here the layout table;
- then `## Install`, `## Use`, `## Notes`, `## License`, in that order. `## How it
  works` may be inserted between `Use` and `Notes` when there is something worth
  explaining;
- commands in a block carry `#` comments that state their cost (size, minutes);
- `## Notes` states the limitations, why the large artefacts are untracked, the licences of
  the dependencies, and the development environment, in the first person and plainly;
- `## License` is that heading followed by `MIT` and nothing else;
- no badges, no emoji, no table of contents, hard wrap at 100 columns.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), in English, with the subsystem as
the scope:

```
<type>(<scope>): <imperative summary, <= 50 chars, no trailing period>

<body: why the change is needed, not what the diff already shows; wrap at 72>

<footer: BREAKING CHANGE: ..., Closes #12, Refs: ...>
```

- `type`: `feat`, `fix`, `build`, `docs`, `refactor`, `perf`, `test`, `chore`,
  `ci`, `style`, `revert`
- `scope`: `rtl-sim`, `qemu-sim`, `docs`, `repo`
- One logical change per commit.
- Mark incompatible changes with `!` after the scope, or with a `BREAKING CHANGE:` footer.
- `.gitmessage` holds the template; `git config commit.template .gitmessage` makes
  `git commit` open the editor with it.