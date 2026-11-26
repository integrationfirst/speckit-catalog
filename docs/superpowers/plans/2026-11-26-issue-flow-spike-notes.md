# issue-flow: workflow engine spike notes

Spec Kit CLI: specify 1.4.4. Scratch project: `specify init --here --integration claude --force --non-interactive --ignore-agent-tools` (note: `--ai` does not exist in 1.4.4; the flag is `--integration`). Spike files (`spike/*.yml`) were not committed. A shim `claude` on PATH was used to capture dispatch argv without running the real agent.

## Corrections to the plan (read first)

1. A `gate` with `options: [continue]` FAILS validation ("on_reject='abort' but options has no 'reject' or 'abort' choice"). Add `on_reject: skip`.
2. A paused gate can only be passed non-interactively via `verdict_input: <input-name>` plus `specify workflow resume <id> -i <input-name>=<option>`. A plain `resume` with stdin not a TTY re-executes the gate and PAUSES again.
3. The verdict input is STICKY (stored in run inputs). Once set, every later gate execution in that run auto-passes with no pause. In a loop this makes the loop spin (observed: 50 iterations, run `completed`, status silently exhausted at `max_iterations`). Under a real TTY (no `verdict_input`) the gate prompts on every iteration, which is the intended loop-and-wait behavior.
4. `command:` is NOT templated (see Q3). Use a `prompt` step or `switch` for a dynamic command.
5. Loop-body step ids from iteration 1 onward are suffixed (`loop:check:1`, ...), cosmetic only.

## Q1: does resume re-enter the loop and re-run `check`?

Resume re-executes ONLY the paused step (the gate), not `check`. After the gate completes, the do-while continues normally: the condition is evaluated, and if still true a new iteration runs `check` again.

Evidence: run `specify workflow run spike/loop.yml < /dev/null` with `status.txt=pending` -> `Status: paused`, run id printed. Log shows `check`, `maybe-wait`, `wait`(paused). Set `echo approved > spike/status.txt`, then `specify workflow resume <id> -i go=continue < /dev/null`: log shows `wait` re-executed, then `loop:check:1`, `loop:maybe-wait:1` (if skipped), `Status: completed`. The condition used the stale pending result from iteration 0, so one more iteration ran and re-read status = approved.

Consequence for issue-flow: a status change made while paused is picked up on the iteration right after resume. Fine.

## Q2: are `steps.<id>` inside a loop body addressable from the do-while condition?

Yes. `{{ steps.check.output.data.status != 'approved' }}` works; it sees the latest iteration's `check` output (after iteration 1 the condition saw `approved` and exited). `output_format: json` populates `output.data` (also `exit_code`, `stdout`, `stderr`).

## Q3: is a templated `command:` accepted?

Accepted by validation but NOT resolved. `command: "{{ inputs.generator }}"` dispatched `claude -p "/speckit-{{ inputs-generator }} stage=one"` (the literal template, dots mangled to dashes). `input.args` IS templated (`stage=one` resolved). Only `input:` values and `integration`/`model` are evaluated.

Working alternatives (verified with the shim):

```yaml
# A) prompt step: whole prompt is templated
- id: gen
  type: prompt
  integration: claude
  prompt: "/{{ inputs.generator }} stage={{ inputs.stage }}"
# dispatched: claude -p "/speckit-specify stage=one"   (generator input = speckit-specify)

# B) switch with static command steps
- id: sw
  type: switch
  expression: "{{ inputs.generator }}"
  cases:
    speckit-specify:
      - id: c1
        command: speckit.specify      # dispatched as /speckit-specify
        integration: claude
        input: { args: "x" }
  default:
    - id: c2
      command: speckit.plan
      integration: claude
      input: { args: "y" }
```

Note on naming: with the claude integration a command step with `command: speckit.plan` dispatches `/speckit-plan`; a `command: speckit-plan` dispatches `/speckit-speckit-plan`. So write `command: speckit.plan` (as the sample workflow does). A `prompt` step sends the text verbatim, so the prompt must contain the final slash-command name (`/speckit-plan`).

## Q4: can a called workflow pause at a gate and be resumed from the parent run?

Yes. After `specify workflow add spike/stage.yml`, a parent step

```yaml
- id: s1
  type: workflow
  workflow: spike-stage
  input:
    stage: "one"
    generator: "speckit.nonexistent"
    go: "{{ inputs.go }}"
```

ran the child, which paused at its gate; the PARENT run id is reported as `paused`. `specify workflow resume <parent-id> -i go=continue` re-entered `s1`, re-executed only the child's gate (the child's earlier `gen` command was NOT re-dispatched: shim log still 1 line), ran child `tail` and parent `after`: `Status: completed`. A plain `resume` without the input re-paused. The verdict must be threaded through parent inputs into the child (`go: "{{ inputs.go }}"`), since `-i` applies to the parent run's inputs.

## Q5: shell non-zero exit

The step is FAILED and the run is `failed` (not paused): `Status: failed`, `Error: Shell command exited with code 3.`; `specify workflow run` exits 1; `--json` gives `{"status":"failed","current_step_id":"boom","error":"Shell command exited with code 3."}`. `output.exit_code` and `output.stderr` are recorded in state. `specify workflow resume <id>` RE-EXECUTES the failed step (trace showed `boom` executed on each resume; earlier steps not re-run) and continues on success; if it still fails the run stays `failed` and resume exits 1. This gives a "fail until ready, then resume" polling pattern without a gate, with no sticky-verdict problem.

## Verbatim working YAML (from the scratch project)

### spike/loop.yml: do-while + json shell `check` + nested `if` + gate (`on_reject: skip`, `verdict_input`) (Q1, Q2)

```yaml
schema_version: "1.0"
workflow:
  id: "spike-loop"
  name: "Spike loop"
  version: "0.0.1"
  description: "spike"
inputs:
  go:
    type: string
    default: ""
steps:
  - id: loop
    type: do-while
    condition: "{{ steps.check.output.data.status != 'approved' }}"
    max_iterations: 50
    steps:
      - id: check
        type: shell
        run: "sh spike/check.sh"
        output_format: json
      - id: maybe-wait
        type: if
        condition: "{{ steps.check.output.data.status == 'pending' }}"
        then:
          - id: wait
            type: gate
            message: "pending"
            options: [continue]
            on_reject: skip
            verdict_input: go
```

### spike/check.sh

```sh
#!/bin/sh
printf '{"status":"%s"}\n' "$(cat spike/status.txt | tr -d '\n')"
```

### spike/stage.yml: called workflow with inputs, command step, gate with verdict (Q3, Q4)

```yaml
schema_version: "1.0"
workflow:
  id: "spike-stage"
  name: "Spike stage"
  version: "0.0.1"
  description: "spike"
inputs:
  stage:
    type: string
    required: true
  generator:
    type: string
    required: true
  go:
    type: string
    default: ""
steps:
  - id: gen
    command: "{{ inputs.generator }}"
    integration: claude
    input:
      args: "stage={{ inputs.stage }}"
  - id: review
    type: gate
    message: "Review stage {{ inputs.stage }}"
    options: [continue]
    on_reject: skip
    verdict_input: go
  - id: tail
    type: shell
    run: "echo tail-{{ inputs.stage }} >> spike/trace.txt"
```

### spike/parent.yml: `workflow` call step with `input:` pass-down of the verdict (Q4)

```yaml
schema_version: "1.0"
workflow:
  id: "spike-parent"
  name: "Spike parent"
  version: "0.0.1"
  description: "spike"
inputs:
  go:
    type: string
    default: ""
steps:
  - id: s1
    type: workflow
    workflow: spike-stage
    input:
      stage: "one"
      generator: "speckit.nonexistent"
      go: "{{ inputs.go }}"
  - id: after
    type: shell
    run: "echo parent-after >> spike/trace.txt"
```

### spike/prompt.yml: Q3 alternatives (prompt step with templated command name; switch with static command steps)

```yaml
schema_version: "1.0"
workflow:
  id: "spike-prompt"
  name: "Spike prompt"
  version: "0.0.1"
  description: "spike"
inputs:
  generator:
    type: string
    default: "speckit-specify"
steps:
  - id: gen
    type: prompt
    integration: claude
    prompt: "/{{ inputs.generator }} stage=one"
  - id: sw
    type: switch
    expression: "{{ inputs.generator }}"
    cases:
      speckit-specify:
        - id: c1
          command: speckit.specify
          integration: claude
          input:
            args: "x"
    default:
      - id: c2
        command: speckit.plan
        integration: claude
        input:
          args: "y"
```

### spike/fail.yml: minimal failing shell step; resume re-runs only `boom` (Q5)

```yaml
schema_version: "1.0"
workflow:
  id: "spike-fail"
  name: "Spike fail"
  version: "0.0.1"
  description: "spike"
steps:
  - id: before
    type: shell
    run: "echo before >> spike/trace.txt"
  - id: boom
    type: shell
    run: "echo boom >> spike/trace.txt; test \"$(cat spike/ok.txt)\" = yes || { echo 'not ok' >&2; exit 3; }"
  - id: after
    type: shell
    run: "echo after >> spike/trace.txt"
```

### spike/loopfail.yml: waiting on external approval inside a do-while (shell step exits non-zero while pending)

```yaml
schema_version: "1.0"
workflow:
  id: "spike-loopfail"
  name: "Spike loop fail"
  version: "0.0.1"
  description: "spike"
steps:
  - id: loop
    type: do-while
    condition: "{{ steps.check.output.data.status != 'approved' }}"
    max_iterations: 50
    steps:
      - id: check
        type: shell
        run: "sh spike/check.sh"
        output_format: json
      - id: wait
        type: shell
        run: "test \"$(cat spike/status.txt)\" = approved || { echo 'still pending' >&2; exit 3; }"
```

Verified inside a do-while: `specify workflow run spike/loopfail.yml < /dev/null` with `status.txt=pending` gives `Status: failed` (exit 1, `Shell command exited with code 3.`). `resume <id>` while still pending re-runs only `wait` and fails again (exit 1). After `echo approved > spike/status.txt`, `resume <id>` re-runs `wait`, then the loop proceeds (`loop:check:1`, `loop:wait:1`) and ends `Status: completed` (exit 0). Log step order: `loop check wait wait wait loop:check:1 loop:wait:1`.

## Other findings

- do-while `max_iterations` exhaustion ends the run as `completed`, not failed. Guard with an explicit final check step if that matters.
- `specify workflow resume --json` and `run --json` exist; `-i key=value` is repeatable.
- Recommended pattern for waiting on external approval (GitHub status): a shell step that exits non-zero while pending; `specify workflow resume <id>` re-runs only that step. Verified at top level (Q5) and inside a do-while body (loopfail.yml above). It avoids the sticky-verdict problem. Gates are for human review pauses; `-i <verdict>=...` is sticky for the rest of the run, so do not use it to pass a gate inside a loop you expect to pause again.
