@echo off
rem ===========================================================================
rem  prismlib - install onto a PRISM C100 that has no internet access
rem
rem  Copyright (c) 2026 RTES Co., Ltd. All rights reserved.
rem
rem      install_scp.bat                    admin@192.168.0.10  (as shipped)
rem      install_scp.bat admin@10.0.0.5     another address or account
rem      install_scp.bat prism-release-1.1.tar.gz
rem                                         firmware from this bundle instead of
rem                                         the newest one in release\
rem
rem  The two arguments can be given in either order. One that ends in .tar.gz
rem  is the release bundle; the other is the board.
rem
rem  Run it from this folder - the one holding install.sh.
rem
rem  THIS PC needs internet. The PRISM does not: the packages are downloaded
rem  here and carried over with the files.
rem
rem      0. if the board's SSH host key changed (re-imaged, or another unit
rem         at this address), drop the old key from known_hosts
rem      1. ask the board which packages it is missing
rem      2. download exactly those on this PC
rem      3. undo an earlier install, if there is one, and empty its folder
rem         (you are asked to confirm this first)
rem      4. copy this folder, packages included, to the board
rem      5. run install.sh there
rem      6. update the firmware from the release bundle - the newest
rem         release\prism-release-x.y.tar.gz here, or the one given - and
rem         reboot the board if the firmware was written. prismlib is not
rem         touched again (swupdate --firmware-only).
rem
rem  If the board does have internet, none of this is needed - copy the folder
rem  across yourself and run "sudo bash ./install.sh" on it.
rem
rem  Needs Windows 10 or later: ssh, scp, curl and robocopy come with it.
rem ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

rem Arguments first, before moving to this folder - a bundle path given relative
rem to where the user stands must resolve there. Either order: a .tar.gz is the
rem bundle, anything else is the board.
set "TARGET="
set "BUNDLE="
for %%A in (%*) do (
    set "ARG=%%~A"
    if /i "!ARG:~-7!"==".tar.gz" (set "BUNDLE=%%~fA") else (set "TARGET=%%~A")
)
if "!TARGET!"=="" set "TARGET=admin@192.168.0.10"

rem Run from the folder this file sits in, however it was launched.
pushd "%~dp0"

rem No bundle given: take the newest release\prism-release-x.y.tar.gz by version
rem number (1.10 is newer than 1.9 - a name sort would get that wrong). A name
rem that is not x.y sorts first, so it is never picked over a real release.
if "!BUNDLE!"=="" if exist "release\prism-release-*.tar.gz" (
    for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "Get-ChildItem 'release\prism-release-*.tar.gz' | Sort-Object { try { [version]($_.Name.Replace('prism-release-','').Replace('.tar.gz','')) } catch { [version]'0.0' } } | Select-Object -Last 1 -ExpandProperty FullName"`) do set "BUNDLE=%%F"
)
if not "!BUNDLE!"=="" if not exist "!BUNDLE!" (
    echo.
    echo   ERROR: the release bundle is not there: !BUNDLE!
    popd
    endlocal
    exit /b 1
)
set "BUNDLE_NAME="
if not "!BUNDLE!"=="" for %%B in ("!BUNDLE!") do set "BUNDLE_NAME=%%~nxB"

for %%I in ("%CD%") do set "FOLDER=%%~nxI"
set "STAGE=%TEMP%\%FOLDER%"
set "URIS=%TEMP%\prismlib_uris.txt"

echo.
echo   prismlib offline install - prismlib and the measurement unit firmware
echo.
echo   usage: install_scp.bat [user@address] [prism-release-x.y.tar.gz]
echo            user@address   the board ^(default admin@192.168.0.10^)
echo            .tar.gz        release bundle for the firmware ^(default: the
echo                           newest release\prism-release-x.y.tar.gz here^)
echo.
echo       target   %TARGET%
echo       source   %CD%
if "!BUNDLE!"=="" (
    echo       firmware none - no release bundle found, the firmware is not updated
) else (
    echo       firmware !BUNDLE!
)
echo.

if not exist "install.sh" (
    echo   ERROR: install.sh is not here.
    echo          Run this from the folder you unpacked.
    goto :fail
)

rem -- 0. stale SSH host key? -------------------------------------------------
rem A re-imaged board, or another unit at the same address, has a different
rem host key, and ssh then refuses to connect. Detect that here and drop the
rem old key from known_hosts; ssh will ask to accept the new one. A matching
rem key, or no entry yet, needs nothing.
for /f "tokens=1,2 delims=@" %%A in ("%TARGET%") do (
    set "HOST=%%B"
    if "%%B"=="" set "HOST=%%A"
)
ssh -n -o BatchMode=yes -o ConnectTimeout=10 %TARGET% true >nul 2>"%TEMP%\prismlib_ssh.txt"
findstr /c:"IDENTIFICATION HAS CHANGED" "%TEMP%\prismlib_ssh.txt" >nul
if not errorlevel 1 (
    echo   NOTE: %HOST% has a different SSH host key than last time
    echo         ^(re-imaged board, or another unit at this address^).
    echo         The old key is removed from known_hosts - accept the new one
    echo         when ssh asks.
    ssh-keygen -R %HOST% >nul 2>&1
    echo.
)
del "%TEMP%\prismlib_ssh.txt" >nul 2>&1

rem -- 1. what does the board need? -------------------------------------------
rem The package list is APT_PACKAGES in install.sh; the board reports which of
rem them it does not have yet.
set "PKGS="
for /f "tokens=1,* delims==" %%A in ('findstr /b /c:"APT_PACKAGES=" install.sh') do (
    set "PKGS=%%B"
)
set PKGS=%PKGS:"=%
if "%PKGS%"=="" (
    echo   ERROR: could not read APT_PACKAGES from install.sh
    goto :fail
)

echo   [1/6] asking the board what it is missing...
rem -n keeps ssh off stdin, so the confirmation prompt below still gets the
rem keyboard (or piped input).
rem Keep "^" and inner double quotes out of this command: cmd does not treat \" as
rem a quote, so text after it is outside quotes and cmd strips every "^" there.
rem That turned the old grep pattern into one that matched nothing, and every
rem board was reported as "no packages needed" (2026-09-27). -qq leaves only the
rem URI lines: 'url' file size hash. \047 is the single quote.
ssh -n -o ConnectTimeout=10 %TARGET% "apt-get install -qq --print-uris -y %PKGS% 2>/dev/null | grep -F .deb | cut -d' ' -f1,2 | tr -d '\047'" > "%URIS%"
if errorlevel 1 (
    echo   ERROR: cannot reach %TARGET%
    echo          Check the cable and the address, then try again.
    echo          The board ships on 192.168.0.10 - this PC must be on the
    echo          same subnet, for example 192.168.0.11.
    goto :fail
)

rem If the board already has this folder, the earlier install is removed before
rem the new one goes on - ask first. Without the folder there is nothing to
rem remove and the install just proceeds.
ssh -n -o ConnectTimeout=10 %TARGET% "[ -d ~/%FOLDER% ]" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   WARNING: an earlier installation exists on %TARGET%.
    echo            Continuing will remove it:
    echo              - the folder ~/%FOLDER% on the board
    echo              - the installed library ^(uninstall.sh is run^)
    echo            and replace it with this folder.
    echo.
    set "ANS="
    set /p "ANS=       Remove it and continue? [y/N]: "
    if /i not "!ANS!"=="y" if /i not "!ANS!"=="yes" goto :cancel
    echo.
)

rem -- 2. stage what actually travels -----------------------------------------
rem Copies this folder to a temp folder without .git, caches and build output.
rem release\ stays here - the bundle goes over on its own in step 6, to /tmp, and
rem is removed there; it does not belong in the install folder.
rem Git's own files (.gitattributes, .gitignore) stay here too - the board has no use for them.
echo   [2/6] preparing the files...
if exist "%STAGE%" rd /s /q "%STAGE%"
robocopy "%CD%" "%STAGE%" /E /XD .git .vscode __pycache__ debs build release /XF *.pyc *.o *.so .gitattributes .gitignore /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo   ERROR: could not prepare the files in %STAGE%
    goto :fail
)

set /a COUNT=0
for /f "usebackq tokens=1,2" %%U in ("%URIS%") do (
    if not exist "%STAGE%\debs" mkdir "%STAGE%\debs"
    echo         downloading %%V
    curl -sSL -o "%STAGE%\debs\%%V" "%%U"
    if errorlevel 1 (
        echo   ERROR: download failed - %%U
        goto :fail
    )
    set /a COUNT+=1
)
del "%URIS%" >nul 2>&1

if %COUNT%==0 (
    echo         no packages needed - the board already has them
) else (
    echo         %COUNT% package^(s^) ready
)

rem -- 3. undo the earlier install --------------------------------------------
rem Runs the uninstall.sh already on the board (none on a first run), then
rem removes the board's folder so it is replaced, not merged. Confirmed above.
echo   [3/6] removing the earlier install, if any...
ssh -t %TARGET% "if [ -f ~/%FOLDER%/uninstall.sh ]; then cd ~/%FOLDER% && sudo bash ./uninstall.sh; else echo '   (nothing installed yet)'; fi"
ssh -t %TARGET% "sudo rm -rf ~/%FOLDER%"

rem -- 4. copy ---------------------------------------------------------------
echo   [4/6] copying to %TARGET%:~/%FOLDER% ...
pushd "%TEMP%"
scp -q -r "%FOLDER%" "%TARGET%:~/"
if errorlevel 1 (
    popd
    echo   ERROR: copy failed.
    goto :fail
)
popd

rem -- 5. install ------------------------------------------------------------
rem Strips CR line endings from the shell scripts first, then runs install.sh
rem (which removes debs/ and the apt package cache when it ends, even on an error).
echo   [5/6] running install.sh on the board...
echo.
ssh -t %TARGET% "cd ~/%FOLDER% && sed -i 's/\r$//' *.sh 2>/dev/null; chmod +x *.sh 2>/dev/null; sudo bash ./install.sh"
if errorlevel 1 goto :fail

if exist "%STAGE%" rd /s /q "%STAGE%"

rem -- 6. firmware -----------------------------------------------------------
rem prism-swupdate was put on the board by install.sh just now. --firmware-only
rem leaves the prismlib that step 5 installed alone. The firmware is skipped if
rem the board already runs this image; if it was written, the board reboots
rem (the CM4 reboot resets the measurement unit, which then runs the new image).
rem Same rule as step 1: no "^" and no inner double quotes in the remote command.
echo.
if "!BUNDLE!"=="" (
    echo   [6/6] firmware - skipped, no release bundle in release\
    echo         Give one: install_scp.bat prism-release-x.y.tar.gz
    goto :done
)
echo   [6/6] updating the firmware from !BUNDLE_NAME! ...
scp -q "!BUNDLE!" "%TARGET%:/tmp/!BUNDLE_NAME!"
if errorlevel 1 (
    echo   ERROR: could not copy the bundle to the board.
    goto :fail
)
ssh -t %TARGET% "sudo prism-swupdate /tmp/!BUNDLE_NAME! --yes --firmware-only > /tmp/prism_swu.log 2>&1; rc=$?; cat /tmp/prism_swu.log; if [ $rc -eq 0 ] && grep -q 'Updating firmware' /tmp/prism_swu.log; then echo; echo '  Rebooting the board to run the new firmware...'; sudo systemd-run --on-active=3 systemctl reboot > /dev/null; fi; rm -f /tmp/!BUNDLE_NAME! /tmp/prism_swu.log; exit $rc"
if errorlevel 1 (
    echo   ERROR: the firmware update failed - see above.
    goto :fail
)

:done
echo.
echo   Done. Check it with:
echo       ssh %TARGET% "python3 -c \"import prismlib; print(prismlib.__version__)\""
echo       ssh %TARGET% prism-swupdate --info
echo.
popd
endlocal
exit /b 0

:cancel
echo.
echo   Cancelled. Nothing was changed on the board.
if exist "%STAGE%" rd /s /q "%STAGE%"
del "%URIS%" >nul 2>&1
popd
endlocal
exit /b 1

:fail
echo.
if exist "%STAGE%" rd /s /q "%STAGE%"
del "%URIS%" >nul 2>&1
popd
endlocal
exit /b 1
