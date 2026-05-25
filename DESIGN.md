---
title: Capability Mesh Alpha
sidebar_position: 95
---

# Capability Mesh Alpha

Capability Mesh Alpha is a privacy-first foundation for connecting task-capable nodes as a capability network.
The mesh core is an independent Python package and protocol layer; Hermes Agent is one adapter/client of that layer.

The first principle is:

> No skill, memory, session, trace, local workflow, or private experience is shared by default. Nodes contribute task capability; knowledge contribution requires explicit human consent.

## Architecture

- `hermes_mesh` is the independent mesh core package. It contains schemas, validators, local registry helpers, dispatch prompt construction, result filtering, verification primitives, and the standalone CLI.
- `hermes_cli.capability_mesh` is a compatibility shim for existing Hermes imports. It re-exports the core API and only adds Hermes profile-scoped default paths under `HERMES_HOME/capability-mesh`.
- `hermes capability-mesh ...` is the Hermes adapter CLI. It preserves existing commands while using the independent core primitives.
- `python -m hermes_mesh.cli ...` is the standalone CLI. It does not require Hermes and stores registry state under `--mesh-home`, `$HERMES_MESH_HOME`, or `~/.hermes-mesh`.
- The dashboard plugin is only a UI client for mesh status and registered manifests. It is not the mesh core and does not expose private Hermes state.

## What Alpha includes

- A `Capability Manifest` that describes what task types a node can perform.
- A `Task Contract` validator with explicit allowed and forbidden result fields.
- A task-result privacy filter that strips forbidden fields and redacts secret-like text.
- An `Optional Skill Proposal` validator that requires human consent before team/public skill contribution.

## What Alpha intentionally does not include

- No token, DAO, or reward system.
- No automatic skill upload.
- No memory/session/trace sharing.
- No arbitrary remote execution protocol.
- No default sharing of private local workflows.

## CLI

Hermes compatibility CLI:

Generate a privacy-first node manifest:

```bash
hermes capability-mesh manifest \
  --node-id local-node-1 \
  --display-name "Local Hermes" \
  --task-type code_review \
  --task-type test_running \
  --tool terminal \
  --tool file \
  --output capability-manifest.yaml
```

Validate a manifest:

```bash
hermes capability-mesh validate capability-manifest.yaml --kind manifest
```

Filter a task result through a task contract:

```bash
hermes capability-mesh filter-result result.yaml --contract task-contract.yaml
```

Validate a skill proposal:

```bash
hermes capability-mesh validate skill-proposal.yaml --kind skill-proposal
```

Standalone core CLI:

```bash
python -m hermes_mesh.cli --mesh-home /tmp/mesh register capability-manifest.yaml
python -m hermes_mesh.cli --mesh-home /tmp/mesh list --json
python -m hermes_mesh.cli validate capability-manifest.yaml --kind manifest
python -m hermes_mesh.cli filter-result result.yaml --contract task-contract.yaml
```

For public or team contribution, an optional skill proposal must include:

```yaml
human_consent: true
human_review_note: "Reviewed and approved for sharing."
```

For local-only proposals, use:

```yaml
proposed_visibility: local_private
human_consent: false
```
