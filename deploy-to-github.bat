@echo off
setlocal enabledelayedexpansion

:: ---- Edit these two if you want a different name or visibility ----
set REPO_NAME=commercial-return-dashboard
set VISIBILITY=private
:: ---------------------------------------------------------------------

cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo git is not installed. Install it first: https://git-scm.com/downloads
    exit /b 1
)

git init -b main
git add -A
git commit -m "Initial commit: commercial return dashboard (employer + employee views)"

where gh >nul 2>nul
if errorlevel 1 (
    echo GitHub CLI ^(gh^) not found, so finish this part manually:
    echo   1. Go to https://github.com/new and create an EMPTY repo named "%REPO_NAME%"
    echo      ^(do NOT check "Add a README" or any other init option^).
    echo   2. Then run these two commands ^(replace ^<your-username^> if not wongjoan^):
    echo        git remote add origin https://github.com/^<your-username^>/%REPO_NAME%.git
    echo        git push -u origin main
) else (
    echo GitHub CLI ^(gh^) found - creating the repo and pushing in one step.
    gh auth status >nul 2>nul
    if errorlevel 1 gh auth login
    gh repo create %REPO_NAME% --%VISIBILITY% --source=. --remote=origin --push
    echo.
    for /f "delims=" %%i in ('gh api user --jq .login') do set GHUSER=%%i
    echo Pushed. Repo: https://github.com/!GHUSER!/%REPO_NAME%
)

echo.
echo Next: go to https://vercel.com/new, click "Import" next to this GitHub repo, and deploy.
echo No build settings needed. Set SESSION_SECRET and DEMO_ACCESS_CODE in Vercel env vars - see README.md.

endlocal
