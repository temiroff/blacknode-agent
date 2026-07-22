# Adapters

Transport adapters for the `memory` component of `blacknode-agent`.

One folder per transport, each mirroring the component layout:

    adapters/ros2/nodes/
    adapters/ros2/templates/

Declare it in `blacknode-package.toml`:

    [components.memory.adapters.ros2]
    description = "ROS 2 adapter for memory."
    default = false
    capabilities = ["adapter.memory.ros2"]
    nodes = ["components/memory/adapters/ros2/nodes"]

Adapters stay `default = false`: the capability package owns them, and
`blacknode-ros2` provides only the shared transport underneath.
