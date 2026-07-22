# Adapters

Transport adapters for the `confirmation` component of `blacknode-agent`.

One folder per transport, each mirroring the component layout:

    adapters/ros2/nodes/
    adapters/ros2/templates/

Declare it in `blacknode-package.toml`:

    [components.confirmation.adapters.ros2]
    description = "ROS 2 adapter for confirmation."
    default = false
    capabilities = ["adapter.confirmation.ros2"]
    nodes = ["components/confirmation/adapters/ros2/nodes"]

Adapters stay `default = false`: the capability package owns them, and
`blacknode-ros2` provides only the shared transport underneath.
