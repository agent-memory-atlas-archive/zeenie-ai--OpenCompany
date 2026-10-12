# Original Gmail issue diagnostics

These scripts preserve the investigation that confirmed issues #143–145 before
the fixes. They assert the original behavior and are historical diagnostics,
not the current regression suite.

To reproduce the original observations, use a separate checkout of pre-fix
revision `988f7c5422ba055095e4d497eea6d390e5224107`, copy these diagnostic files
into `.tmp_issue_validation/`, and follow the commands in each script.
The payload diagnostic also inspects the reported revision `d36f7db`.

The permanent regression coverage for the corrected behavior lives under
`server/tests/services/employees/` and `server/tests/temporal/`.

`test_event_flag.py` records the separate framework-disabled investigation;
that behavior remains outside the scope of the three implemented fixes.
The diagnostics use synthetic email content and intercept external services.
