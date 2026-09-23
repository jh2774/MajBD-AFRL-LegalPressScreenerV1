<#
.SYNOPSIS
    Clone the handover bundle and push it to a GitHub repository.

.DESCRIPTION
    A git bundle is a clone source, not a file to commit. Uploading one through
    GitHub's web interface just stores a binary blob — the project does not
    appear. This does it properly: clone the bundle, point the clone at GitHub,
    push.

    Run it in your own terminal. The push needs an interactive GitHub sign-in,
    which is why it cannot be done for you; Git Credential Manager opens a
    browser the first time and remembers it afterwards.

.EXAMPLE
    .\push_to_github.ps1 -Bundle "$HOME\Downloads\foci-screen-ready.bundle"

.EXAMPLE
    .\push_to_github.ps1 -Bundle .\foci-screen-ready.bundle -Into C:\code\foci-screen -WhatIfOnly
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Bundle,

    [string] $Repo = "https://github.com/jh2774/MajBD-AFRL-LegalPressScreenerV1.git",

    [string] $Into = (Join-Path (Get-Location) "foci-screen"),

    # Show what would happen and stop before pushing.
    [switch] $WhatIfOnly
)

# Deliberately not "Stop". Windows PowerShell turns anything a native command
# writes to stderr into a terminating error, and git writes ordinary progress
# there — "git bundle verify" reports success on stderr. Exit codes are checked
# explicitly instead, which is the only reliable signal here.
$ErrorActionPreference = "Continue"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nFAILED: $text" -ForegroundColor Red; exit 1 }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "git is not on PATH. Install Git for Windows: https://git-scm.com/download/win"
}
if (-not (Test-Path -LiteralPath $Bundle)) { Fail "No bundle at: $Bundle" }
$Bundle = (Resolve-Path -LiteralPath $Bundle).Path

Step "Checking the bundle"
# 2>&1 throughout: git reports progress and success on stderr, and without this
# Windows PowerShell renders every such line as though it were an error.
git bundle verify $Bundle 2>&1 | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { Fail "The bundle is damaged. Download it again." }

if (Test-Path -LiteralPath $Into) {
    Fail "$Into already exists. Delete it, or pass -Into with a different path."
}

Step "Cloning to $Into"
git clone --branch main $Bundle $Into 2>&1 | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { Fail "Clone failed." }

Push-Location $Into
try {
    # The clone's origin points at the bundle file; repoint it at GitHub.
    git remote set-url origin $Repo

    Step "What will be pushed"
    git log --oneline -5
    Write-Host ("`n{0} files, head {1}" -f (git ls-files).Count, (git rev-parse --short HEAD))

    Step "Checking this is additive, not a rewrite"
    $env:GIT_TERMINAL_PROMPT = "0"
    $remoteHead = (git ls-remote --heads origin main 2>$null)
    $env:GIT_TERMINAL_PROMPT = "1"
    if ($remoteHead) {
        $sha = ($remoteHead -split "\s+")[0]
        git merge-base --is-ancestor $sha HEAD 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Remote tip $($sha.Substring(0,7)) is an ancestor: the push fast-forwards." -ForegroundColor Green
        } else {
            Write-Host "Remote has commits this bundle does not contain." -ForegroundColor Yellow
            Write-Host "Nothing has been pushed. Fetch and merge before pushing:" -ForegroundColor Yellow
            Write-Host "    cd $Into; git fetch origin main; git merge FETCH_HEAD"
            exit 2
        }
    } else {
        Write-Host "Remote branch is empty or unreadable; the push will create main."
    }

    if ($WhatIfOnly) {
        Step "Stopping before the push (-WhatIfOnly)"
        Write-Host "When ready:  cd $Into; git push origin main"
        exit 0
    }

    Step "Pushing (a browser may open for GitHub sign-in)"
    git push origin main 2>&1 | ForEach-Object { "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Fail "Push rejected. If it says 'non-fast-forward', run: git fetch origin main; git merge FETCH_HEAD; git push origin main"
    }

    Step "Done"
    Write-Host "Repository: $($Repo -replace '\.git$', '')"
    Write-Host "Watch the Actions tab: this run is the first build of both Docker images."
}
finally {
    Pop-Location
}
