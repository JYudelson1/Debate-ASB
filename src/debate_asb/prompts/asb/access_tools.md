**You have access to:**
- A research paper (`PAPER.md`)
- The complete codebase that generated the paper's results

You access them through three read-only tools: `list_files`, `read_file` and `search`. You cannot run code, and you cannot view images.

You can make up to {max_steps} rounds of tool calls. When you are done investigating, stop calling tools and give your answer.

### Efficient File Reading

**Do NOT read entire large files.** Reading whole files will overflow your context window and cause failures.

**Workflow for understanding code:**
1. **Use `search` first** to locate relevant code
2. **Read only relevant line ranges** once you've found them, with `read_file`'s `start_line` and `num_lines`
3. **For files >200 lines:** Always search within the file first, then read just the function or section you need

**This is critical for your success.** Models that try to read entire codebases run out of context and fail.
