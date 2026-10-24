## Schedule Preview and Deployment Confirmation

### 1. Default Configuration

The application must support the following example configuration:

- **Start Month:** January
- **End Month:** December
- **Year:** 2027
- **Schedule Name Prefix:** `Monthly Close`

The application must automatically generate the schedule names and display all 12 planned schedules before deployment.

### 2. Exact Schedule Preview

The preview must display the following entries in chronological order:

|   # | Schedule Name                  | Period    | Year | Status |
| --: | ------------------------------ | --------- | ---: | ------ |
|   1 | Monthly Close - January 2027   | January   | 2027 | Ready  |
|   2 | Monthly Close - February 2027  | February  | 2027 | Ready  |
|   3 | Monthly Close - March 2027     | March     | 2027 | Ready  |
|   4 | Monthly Close - April 2027     | April     | 2027 | Ready  |
|   5 | Monthly Close - May 2027       | May       | 2027 | Ready  |
|   6 | Monthly Close - June 2027      | June      | 2027 | Ready  |
|   7 | Monthly Close - July 2027      | July      | 2027 | Ready  |
|   8 | Monthly Close - August 2027    | August    | 2027 | Ready  |
|   9 | Monthly Close - September 2027 | September | 2027 | Ready  |
|  10 | Monthly Close - October 2027   | October   | 2027 | Ready  |
|  11 | Monthly Close - November 2027  | November  | 2027 | Ready  |
|  12 | Monthly Close - December 2027  | December  | 2027 | Ready  |

These names must be generated dynamically from the user's configuration, not hardcoded.

### 3. Pre-Deployment Validation

Before enabling deployment, the application must:

1. Validate the selected Excel file and identify the source Task Manager Template.
2. Verify connectivity and authentication with the selected Oracle FCCS environment.
3. Validate that the required template and deployment permissions are available.
4. Resolve the Oracle period and Day Zero Date for each month.
5. Query Oracle to determine whether each target Schedule already exists.
6. Display any validation errors or duplicate schedules.
7. Mark each entry as `Ready`, `Skipped - Already Exists`, or `Blocked - Validation Error`, as appropriate.

The application must not mark a schedule as ready if its required Oracle period, date configuration, or source template cannot be validated.

### 4. Confirmation Dialog

When the user clicks **Deploy Schedules**, show a confirmation dialog before making any changes in Oracle.

Example:

**Confirm Schedule Deployment**

You are about to deploy monthly Task Manager schedules to the selected Oracle FCCS environment.

- Environment: the currently selected FCCS environment
- Source Template: the validated template name
- Schedule Prefix: Monthly Close
- Period: January–December 2027
- Total Schedules Requested: 12
- New Schedules to Create: calculated from the latest validation results
- Existing Schedules to Skip: calculated from the latest validation results

Display a warning that deployment creates Oracle Task Manager schedules and may initiate changes in the selected environment.

Require the user to explicitly confirm by clicking **Confirm and Deploy**. Provide a separate **Cancel** button.

Do not deploy anything merely because the user opens the preview.

### 5. Deployment Progress and Results

After confirmation, process each eligible month individually.

Display:

- Current schedule name
- Current operation
- Progress, such as `4 of 12`
- Successes, skipped schedules, and failures
- Detailed error messages when available

The UI must remain responsive throughout deployment.

Verify the actual outcome of every Oracle operation, including asynchronous jobs where applicable. Do not mark a schedule as completed simply because Oracle accepted a deployment request.

If a schedule fails, report the failure and allow the user to retry eligible failed schedules without recreating schedules that have already been successfully deployed.

### 6. Completion Summary

At the end of the operation, show a summary containing:

- Total schedules requested
- Successfully created schedules
- Existing schedules skipped
- Failed schedules
- Deployment duration
- Location of the deployment log or exported report

Provide buttons for **View Results**, **Export Report**, and **Close**.

### 7. Acceptance Criteria

The feature is accepted when:

- Selecting January–December 2027 and the prefix `Monthly Close` produces the exact 12 names listed above.
- Changing the month range or year updates the preview immediately.
- Duplicate schedules are detected before deployment.
- The user must explicitly confirm deployment.
- No Oracle changes occur before confirmation.
- Successful schedules are verified and reported accurately.
- Failed operations do not cause successful schedules to be duplicated on retry.
- All interface text and confirmation messages are in English.
