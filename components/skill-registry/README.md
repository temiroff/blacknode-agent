# Skill Registry

Component of `blacknode-agent`.

Node sources for this component belong in this folder. Until they move here,
nodes claim the component inline:

    @node(name="MyNode", component="skill-registry", ...)

Once sources live here, declare the folder in `blacknode-package.toml`:

    [components.skill-registry]
    nodes = ["components/skill-registry/nodes"]

and the inline `component=` argument can be dropped — the loader infers it
from the directory.
