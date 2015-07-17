@echo off

call test_cfg.bat

..\ccdash.py build "nmake -f NMakefile err" -w source -U %CDASH_SUBMIT_URL% -S %SITE_NAME% -T %CDASH_BUILD_STAMP% -B %BUILD_NAME% -G %CDASH_GROUP% -o out/build_err.xml %1 %2 %3 %4 %5 %6 %7 

