*** Test Cases ***
Producer observed pass
    Should Be Equal    same    same

Producer observed failure
    Should Be Equal    observed    expected

Producer observed skip
    Skip    Deliberately skipped input leaf
