@echo off

call test_cfg.bat

set CONFIGURE_CMD="cmd /C this_cmd_doesnt_exist"

..\ccdash.py configure %CONFIGURE_CMD% -U %CDASH_SUBMIT_URL% -S %SITE_NAME% -T %CDASH_BUILD_STAMP% -B %BUILD_NAME% -G %CDASH_GROUP% -o out/configure_err.xml %1 %2 %3 %4 %5 %6 %7

