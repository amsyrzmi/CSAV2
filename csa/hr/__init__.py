"""HR contracts, pure policy and verified persistence/API services.

Only trusted server adapters may construct identity, grants and directory facts.
Browser/model input is limited to the explicit Input models in ``models``.
Gateway routes and disabled tenant staff agents reuse these services. Persistent
rollout and provider execution are separate; no provider is invoked here.
"""
