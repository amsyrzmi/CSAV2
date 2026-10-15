# Sources and verification status

Primary input: the attached “Pasted markdown.md”, supplied as STATE.md. It describes the existing architecture; no repository checkout, credentials or server session was supplied for implementation. This pack does not assert that the current code and live schema still exactly match that description.

Official documentation inspected while planning on 12 October 2026:

- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence — checkpointers persist thread state; stores hold longer-lived application data. Use durable storage for production and enforce project authorization around thread access.
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts — pause/resume on a persisted thread; replay/resume affects how nodes must handle side effects. Verify API availability against your pinned package rather than assuming the latest documentation applies.
- Hermes Agent profiles: https://hermes-agent.nousresearch.com/docs/user-guide/profiles — separate agent homes, concurrency and distinction from terminal working directory/OS home. Documentation may be ahead of your installation.
- Mermaid types: https://mermaid.js.org/intro/ — choose only types tested against the installed renderer version.
- Mermaid securityLevel: https://mermaid.js.org/config/schema-docs/config-properties-securitylevel.html — strict settings are a baseline; the app must also control configuration and safe exports.
- PlantUML diagram catalog: https://plantuml.com/ — broader textual UML support; local renderer configuration and sandboxing remain application responsibilities.
- AWS icon library for PlantUML: https://github.com/awslabs/aws-icons-for-plantuml — AWS icons/assets can be vendored and pinned for cloud diagrams.

Architecture, schema names, adapter methods, UI behavior, limits and phase ordering are proposed application design, not native framework features. In particular, the pack does not claim a preexisting Hermes REST API, perfect bug detection, universal diagram coverage, complete n8n migration, AWS deployment support or exactly-once external execution.
