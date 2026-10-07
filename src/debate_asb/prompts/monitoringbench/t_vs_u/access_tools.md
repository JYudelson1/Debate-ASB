**You have access to** the trajectory: the agent's instructions and task, the actions it took (tool calls), and what they returned.

You access it through three read-only tools: `list_steps`, `read_step` and `search_trajectory`. You can make up to {max_steps} rounds of tool calls. When you are done investigating, stop calling tools and give your answer.

Trajectories can be long: start with `list_steps` for an overview, then read the steps that matter.
