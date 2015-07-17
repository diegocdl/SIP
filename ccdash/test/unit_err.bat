@echo off

call test_cfg.bat

..\ccdash.py test "Unit test 2" "cmd /C this_cmd_doesnt_exist"  -U %CDASH_SUBMIT_URL% -S %SITE_NAME% -T %CDASH_BUILD_STAMP% -B %BUILD_NAME% -G %CDASH_GROUP% -o out/unit_err.xml %1 %2 %3 %4 %5 %6 %7

