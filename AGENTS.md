# blacknode-agent Agent Instructions

This is an independent Blacknode extension-package repository.

Keep planners, skill selection, mission review, confirmation, and memory here.
Use robot capability contracts instead of vendor SDKs, device paths, or direct
hardware transports. A planner may request motion but never bypass controller
authorization, safety supervision, or explicit confirmation. Declare component
dependencies and keep model/provider imports optional at load time.

Run package tests with `python -m pytest packages/blacknode-agent/tests`.
