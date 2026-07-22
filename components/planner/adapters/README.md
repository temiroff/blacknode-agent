# Adapters

Transport adapters for the `planner` component of `blacknode-agent`.

One folder per transport, each mirroring the component layout:

    adapters/ros2/nodes/
    adapters/ros2/templates/

Declare it in `blacknode-package.toml`:

    [components.planner.adapters.ros2]
    description = "ROS 2 adapter for planner."
    default = false
    capabilities = ["adapter.planner.ros2"]
    nodes = ["components/planner/adapters/ros2/nodes"]

Adapters stay `default = false`: the capability package owns them, and
`blacknode-ros2` provides only the shared transport underneath.
