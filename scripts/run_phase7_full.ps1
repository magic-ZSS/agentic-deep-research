[CmdletBinding()]
param(
    [switch]$ConfirmCost,

    [switch]$ApproveFull,

    [ValidateSet("local", "langsmith")]
    [string]$Tracking = "local",

    [string]$LangSmithProject = "phase7-local-full",

    [string]$EnvironmentName = "open-deep-research",

    [string]$CalibrationOutput = "artifacts/evaluation/calibration-current",

    [string]$FullOutput = "artifacts/evaluation/full"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PreviousEvalMode = $env:ODR_EVAL_MODE
$PreviousRunFullEval = $env:RUN_FULL_EVAL
$ScriptExitCode = 0
$StartFull = $true

function Stop-Phase7Evaluation {
    param(
        [int]$Code,
        [string]$Message
    )

    $Failure = [System.InvalidOperationException]::new($Message)
    $Failure.Data["Phase7ExitCode"] = $Code
    throw $Failure
}

function Restore-EnvironmentValue {
    param(
        [string]$Name,
        [AllowNull()][string]$Value
    )

    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

function Resolve-CondaPython {
    param([string]$Name)

    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        Stop-Phase7Evaluation -Code 1 -Message (
            "Conda could not be started. Confirm that conda is on PATH."
        )
    }
    $PreviousErrorActionPreference = $ErrorActionPreference
    $DiscoveryCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $Discovery = & conda run --no-capture-output -n $Name python -c (
            "import sys; print(sys.executable)"
        ) 2>$null
        $DiscoveryCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($DiscoveryCode -ne 0) {
        Stop-Phase7Evaluation -Code 1 -Message (
            "Conda environment '$Name' was not found. Run this once first: " +
            "conda env create -f environment.phase7.yml"
        )
    }
    $Candidate = $Discovery |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Last 1
    if ($Candidate) {
        $Candidate = $Candidate.Trim()
    }
    if (
        [string]::IsNullOrWhiteSpace($Candidate) -or
        -not (Test-Path -LiteralPath $Candidate -PathType Leaf)
    ) {
        Stop-Phase7Evaluation -Code 1 -Message (
            "The evaluation environment has no python.exe: $Candidate"
        )
    }
    return $Candidate
}

function Invoke-CheckedPython {
    param(
        [string]$Python,
        [string[]]$Arguments,
        [string]$Label
    )

    Write-Host "`n==> $Label" -ForegroundColor Cyan
    & $Python @Arguments
    $Code = $LASTEXITCODE
    if ($Code -ne 0) {
        Stop-Phase7Evaluation -Code $Code -Message (
            "$Label failed with exit code $Code. Paid steps are never retried automatically."
        )
    }
}

function Require-CleanEvaluationSourceStatus {
    $EvaluationPaths = @(
        "src/open_deep_research",
        "scripts/run_eval.py",
        "scripts/run_phase7_full.cmd",
        "scripts/run_phase7_full.ps1",
        "scripts/validate_phase.py",
        "scripts/compare_ablations.py",
        "scripts/render_eval_report.py",
        "tests/evaluation",
        "tests/baseline/cases.jsonl",
        "pyproject.toml",
        "langgraph.json",
        "environment.phase7.yml",
        "constraints/evaluation-py311.txt"
    )
    $StatusLines = & git status --porcelain=v1 --untracked-files=all -- @EvaluationPaths
    if ($LASTEXITCODE -ne 0) {
        Stop-Phase7Evaluation -Code 4 -Message (
            "Evaluation source status could not be inspected safely."
        )
    }
    if ($StatusLines) {
        Write-Host "`n==> Uncommitted evaluation source:" -ForegroundColor Yellow
        $StatusLines | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
        Stop-Phase7Evaluation -Code 4 -Message (
            "Review and commit the listed evaluation files before a paid run."
        )
    }
}

try {
    if (-not $ConfirmCost) {
        Stop-Phase7Evaluation -Code 2 -Message (
            "Cost confirmation is required. Re-run with -ConfirmCost."
        )
    }

    Push-Location $RepoRoot
    try {
        $PythonExe = Resolve-CondaPython -Name $EnvironmentName
        Require-CleanEvaluationSourceStatus

        Invoke-CheckedPython -Python $PythonExe -Label "Check dependency consistency" -Arguments @(
            "-m", "pip", "check"
        )
        Invoke-CheckedPython -Python $PythonExe -Label "Check evaluation environment and clean source" -Arguments @(
            "-c",
            (
                "from pathlib import Path; " +
                "import deepeval; " +
                "import open_deep_research.evaluation.full_runner; " +
                "from open_deep_research.evaluation.source_gate import require_clean_evaluation_source; " +
                "require_clean_evaluation_source(Path.cwd()); " +
                "print('evaluation environment and source: ready')"
            )
        )

        if ($Tracking -eq "langsmith") {
            if (-not $env:LANGSMITH_API_KEY) {
                Stop-Phase7Evaluation -Code 2 -Message (
                    "Tracking=langsmith requires LANGSMITH_API_KEY in this shell."
                )
            }
            if ([string]::IsNullOrWhiteSpace($LangSmithProject)) {
                Stop-Phase7Evaluation -Code 2 -Message (
                    "Tracking=langsmith requires a non-empty LangSmithProject."
                )
            }
        }

        $env:ODR_EVAL_MODE = "full"
        $env:RUN_FULL_EVAL = "1"

        $CalibrationExists = Test-Path -LiteralPath $CalibrationOutput
        $FullExists = Test-Path -LiteralPath $FullOutput
        if ($FullExists -and -not $CalibrationExists) {
            Stop-Phase7Evaluation -Code 3 -Message (
                "The full output exists but its calibration directory is missing. " +
                "Inspect the artifacts; do not start a new paid calibration."
            )
        }

        if (-not $CalibrationExists -and -not $FullExists) {
            Invoke-CheckedPython -Python $PythonExe -Label "Run offline smoke evaluation" -Arguments @(
                "scripts/run_eval.py",
                "--mode", "smoke",
                "--variants", "all",
                "--dataset-version", "v1",
                "--output", "artifacts/evaluation/smoke"
            )
        }
        else {
            Write-Host (
                "`n==> Existing paid state found; keep it and continue with resume."
            ) -ForegroundColor Yellow
        }

        $CalibrationArguments = @(
            "scripts/run_eval.py",
            "--mode", "calibration",
            "--variants", "baseline,citation_validator",
            "--dataset-version", "v1",
            "--repeats", "1",
            "--max-total-tokens", "3000000",
            "--confirm-cost",
            "--output", $CalibrationOutput
        )
        if ($CalibrationExists) {
            $CalibrationArguments += "--resume"
        }

        Invoke-CheckedPython -Python $PythonExe -Label (
            "Run/resume the 6-run calibration (3,000,000 Token maximum)"
        ) -Arguments $CalibrationArguments

        Write-Host (
            "`n==> Compute the full projection locally (no model/search/tracking call)"
        ) -ForegroundColor Cyan
        $PreflightArguments = @(
            "scripts/run_eval.py",
            "--mode", "full",
            "--preflight-only",
            "--variants", "all",
            "--dataset-version", "v1",
            "--repeats", "3",
            "--max-total-tokens", "42000000",
            "--calibration-output", $CalibrationOutput,
            "--output", $FullOutput
        )
        $PreflightLines = & $PythonExe @PreflightArguments
        $PreflightCode = $LASTEXITCODE
        if ($PreflightCode -ne 0) {
            Stop-Phase7Evaluation -Code $PreflightCode -Message (
                "Full preflight failed with exit code $PreflightCode. " +
                "No full paid call was dispatched."
            )
        }
        try {
            $PreflightText = $PreflightLines -join [Environment]::NewLine
            $Preflight = $PreflightText | ConvertFrom-Json
        }
        catch {
            Stop-Phase7Evaluation -Code 4 -Message (
                "Full preflight returned invalid JSON. No full paid call was dispatched."
            )
        }
        $Preflight | ConvertTo-Json -Depth 10
        if ($Preflight.status -ne "ready_for_separate_full_authorization") {
            Stop-Phase7Evaluation -Code 4 -Message (
                "Full preflight did not return an authorization-ready status."
            )
        }

        if (-not $ApproveFull) {
            $Projection = $Preflight.projection.projected_tokens
            $Answer = Read-Host (
                "Projected usage is $Projection Token; the hard ceiling is 42,000,000. " +
                "Type FULL to start the full matrix, or press Enter to stop"
            )
            if ($Answer -cne "FULL") {
                Write-Host (
                    "Full evaluation was not started. Calibration is complete and reusable."
                ) -ForegroundColor Yellow
                $StartFull = $false
            }
        }

        if ($StartFull) {
            $FullArguments = @(
                "scripts/run_eval.py",
                "--mode", "full",
                "--variants", "all",
                "--dataset-version", "v1",
                "--repeats", "3",
                "--max-total-tokens", "42000000",
                "--confirm-cost",
                "--calibration-output", $CalibrationOutput,
                "--tracking", $Tracking,
                "--output", $FullOutput
            )
            if ($Tracking -eq "langsmith") {
                $FullArguments += @("--langsmith-project", $LangSmithProject)
            }
            if ($FullExists) {
                $FullArguments += "--resume"
            }

            Invoke-CheckedPython -Python $PythonExe -Label (
                "Run/resume the fixed 54-run full matrix"
            ) -Arguments $FullArguments

            Invoke-CheckedPython -Python $PythonExe -Label "Render report and README" -Arguments @(
                "scripts/render_eval_report.py",
                "--input", (Join-Path $FullOutput "report.json"),
                "--output", (Join-Path $FullOutput "report.md"),
                "--readme", "README.md"
            )
            Invoke-CheckedPython -Python $PythonExe -Label "Validate Phase 7" -Arguments @(
                "scripts/validate_phase.py", "--phase", "7"
            )

            Write-Host (
                "`nPhase 7 evaluation, ablations, report, and README are complete."
            ) -ForegroundColor Green
            Write-Host "Authoritative result: $FullOutput/report.json"
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    $RecordedCode = $_.Exception.Data["Phase7ExitCode"]
    if ($null -ne $RecordedCode) {
        $ScriptExitCode = [int]$RecordedCode
    }
    else {
        $ScriptExitCode = 1
    }
    [Console]::Error.WriteLine("Phase 7 evaluation stopped: " + $_.Exception.Message)
}
finally {
    Restore-EnvironmentValue -Name "ODR_EVAL_MODE" -Value $PreviousEvalMode
    Restore-EnvironmentValue -Name "RUN_FULL_EVAL" -Value $PreviousRunFullEval
}

exit $ScriptExitCode
