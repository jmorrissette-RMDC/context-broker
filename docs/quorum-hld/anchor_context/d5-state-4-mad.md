# Concept: The State 4 MAD

**Status:** Concept
**Date:** 2026-03-20
**Maturity:** Concept

---

## Author

**J. Morrissette**

---

## What This Is

State 4 is the final step in the MAD architectural evolution. A State 4 MAD is fully self-contained and configurable — it can operate standalone on any Docker-capable host or participate in a larger ecosystem, with the same image and the same code in both cases. Every external dependency that States 1–3 assumed as fixed infrastructure becomes a configuration choice.

---

## The Evolution

Each state in the MAD architecture solves a specific coupling problem:

**State 0 (Legacy)** — Monolithic single container. Everything in one place. No separation of concerns.

**State 1 (Decomposed)** — Functional decomposition into gateway, processing, and backing services. Separates the MCP interface from the application logic from the data stores. The containers are distinct but the ecosystem dependencies are hard-coded: Node.js gateway uses mad-core libraries, LangGraph calls specific peer MADs by name, builds require Alexandria.

**State 2 (OTS Infrastructure)** — Only the LangGraph container is custom; everything else is off-the-shelf. The gateway becomes nginx with configuration-driven routing. Peer calls use standard langchain-mcp-adapters. The Imperator pattern becomes mandatory. The ecosystem dependencies remain — Sutherland for inference, Alexandria for packages, joshua-net for networking — but the infrastructure layer is clean.

**State 3 (AE/TE Separation)** — Infrastructure (AE) and intelligence (TE) are independently deployable packages loaded at runtime by a bootstrap kernel. The kernel is stable and rarely changes. AE and TE packages are versioned, published to Alexandria, and installed without container restart. This enables zero-downtime cognitive updates and makes eMADs (intelligence-only packages hosted by a pMAD) possible. The ecosystem dependencies still exist but the internal architecture is fully separated.

**State 4 (Configurable Dependencies)** — Every ecosystem dependency becomes a configuration choice:

- **Inference** — Configurable provider interface (OpenAI-compatible `base_url` + model + API key) instead of hard coupling to Sutherland. Works with OpenAI, Google, xAI, Groq, Ollama, vLLM, or any OpenAI-compatible endpoint.
- **Package source** — Configurable between local wheels bundled in the repository, public PyPI, or a private devpi index (Alexandria). The kernel's `install_stategraph()` respects the configured source.
- **Network topology** — Standard Docker Compose bridge networks instead of requiring Swarm overlay networks. No joshua-net dependency. The gateway exposes ports on the host; internal containers communicate on a private bridge network.
- **Storage** — Standard bind mounts (`./data`, `./config`) instead of named Swarm volumes backed by NFS. The paths inside the container are fixed; the host paths are controlled by the compose file.
- **Identity** — UID/GID defined in the Dockerfile and compose file rather than pulled from a central registry.yml.
- **Credentials** — Loaded from a local `.env` file rather than a shared NFS-mounted credentials directory.

The result: `docker compose up` on any machine with Docker installed. No Swarm, no NFS, no Alexandria, no Sutherland, no registry.yml. The same image that runs in the ecosystem runs standalone — the configuration file is the only difference.

---

## The Configuration Principle

State 4 does not remove ecosystem capabilities. It makes them optional. A State 4 MAD deployed inside Joshua26 points its config at Sutherland for inference, Alexandria for packages, and the ecosystem's credential store. The same MAD deployed standalone points its config at OpenAI, bundles its packages locally, and reads credentials from a local file.

The code never changes. The Dockerfiles never change. The StateGraph flows never change. Only `config.yml` changes.

This extends to the Imperator. In ecosystem deployment, the Imperator routes inference through Sutherland like any other flow. In standalone deployment, it reads the same `config.yml` and uses whatever provider is configured. There is no special case for the Imperator — it is just another consumer of the configured provider interface.

---

## What This Enables

**Publication.** A State 4 MAD can be published as a standalone tool that anyone can download and run. The Context Broker is the first example — a self-contained context engineering service that validates the concept papers with runnable code.

**Portability.** The same MAD runs on a laptop, a cloud VM, a CI runner, or inside the Joshua26 ecosystem. No adaptation required beyond configuration.

**Evaluation.** Someone evaluating the architecture can experience it without setting up the ecosystem. They see the same StateGraph flows, the same three-tier assembly, the same knowledge extraction — just backed by a different inference provider.

**Contribution.** With packages bundled locally, a contributor can modify a StateGraph flow, rebuild the container, and see the change immediately. No package registry, no publishing step, no ecosystem access required.

---

## Applicability

Not every MAD needs to be State 4. State 4 is appropriate when:

- The MAD will be published or shared outside the ecosystem
- The MAD needs to run in environments without ecosystem infrastructure
- The MAD's value proposition is independent of the specific ecosystem it runs in

A MAD that is inherently ecosystem-specific (e.g., a service that coordinates between other ecosystem MADs) may not benefit from State 4 configurability. The investment in making every dependency configurable should serve a real need.

---

## Relationship to Other Concepts

- **Agent-Optimal Code Architecture** (`d4-agent-optimal-code-architecture.md`) — State 4 extends the State 0→3 evolution arc with configurable external dependencies as the final decoupling step
- **The Context Broker** (`c1-the-context-broker.md`) — the first State 4 MAD; the standalone Context Broker is the reference implementation of this pattern
- **The MAD Pattern** (`a5-the-mad-pattern.md`) — State 4 does not change what a MAD is; it changes where a MAD can run
