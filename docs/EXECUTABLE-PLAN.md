# Standalone executable implementation order

Parent [#36](https://github.com/grauzone-git/sandboxed-ai-agents/issues/36)
defines the executable and handover requirements. Implement on
`feature/36-standalone-executable`, starting with #38. Each completed issue gets
one commit after its relevant tests and separate Standards and Spec reviews
pass with no remaining findings. Compare each issue with the preceding commit.

| Order | Issue | Work | Dependencies |
| --- | --- | --- | --- |
| 1 | #38 | Launcher-selectable host contract and script freeze | None |
| 2 | #39 | Go version, bundled build, list, and CI | #38 |
| 3 | #40 | Named-volume lifecycle and ownership | #39 |
| 4 | #41 | Workspace binds and protected paths | #40 |
| 5 | #42 | Nested Podman capability | #40 |
| 6 | #43 | Windows runtime and path aliases | #41, #42 |
| 7 | #44 | Opt-in SSH setup | #40 |
| 8 | #45 | Transactional updates and outdated markers | #42, #44 |
| 9 | #46 | Agent and tool management | #40 |
| 10 | #47 | Services, forwarding, and tool setup | #44, #46 |
| 11 | #48 | Explicit checkout adoption | #44, #45 |
| 12 | #49 | Release artifacts and npm/NuGet packages | #39 |
| 13 | #50 | Documentation, human validation, stable release, removal | #43, #45, #47, #48, #49 |

This is one valid serial order. #41, #42, #44, and #46 can follow #40
independently. #49 can start after #39, but final distribution validation needs
the commands users will install.

Use the name-first grammar from #36 and #31. The command-first examples in
#40 and #41 do not supersede that decision. Preserve the security boundaries
and Azure authentication behavior established by the completed issues.

#2 and #3 remain prerequisite-installer planning, #11 is Windows ARM64 work,
and #20 and #24 are undecided proposals. They do not block #36 and are outside
this implementation series. The full suite exposed the existing #18 PowerShell
rendering assertion during #38. Its [test correction](POWERSHELL-STDERR.md) is
a separate prerequisite commit before #38, as requested by the user.
#4 and #5 were folded into #36. #37 was closed in favor of the rewrite.

## Completion gates

Use the public CLI test boundary confirmed for this work: exit status, output,
fake Podman/SSH calls, and filesystem changes. Keep targeted checks in each
implementation cycle and run the full offline suite before the issue commit.
The [host contract](HOST-CONTRACT.md) must keep passing against the scripts.

#50 cannot be completed solely by code changes. The owner must validate real
Linux and Windows image builds, creation, SSH, updates with rollback, and
adoption. One stable executable release must ship before the release that
removes the scripts. Keep the current entry points until those gates hold.
