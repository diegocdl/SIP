@echo off

call test_cfg.bat

..\ccdash.py test "Unit test 1" "cmd /C echo The test has been successful"  -U %CDASH_SUBMIT_URL% -S %SITE_NAME% -T %CDASH_BUILD_STAMP% -B %BUILD_NAME% -G %CDASH_GROUP% -o out/unit_success.xml %1 %2 %3 %4 %5 %6 %7

