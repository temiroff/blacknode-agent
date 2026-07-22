# Adapters

Transport adapters for the `skill-registry` component of `blacknode-agent`.

One folder per transport, each mirroring the component layout:

    adapters/ros2/nodes/
    adapters/ros2/templates/

Declare it in `blacknode-package.toml`:

    [components.skill-registry.adapters.ros2]
    description = "ROS 2 adapter for skill-registry."
    default = false
    capabilities = ["adapter.skill-registry.ros2"]
    nodes = ["components/skill-registry/adapters/ros2/nodes"]

Adapters stay `default = false`: the capability package owns them, and
`blacknode-ros2` provides only the shared transport underneath.
