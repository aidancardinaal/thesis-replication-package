# Specification-correctness attribution worksheet

One row per flagged divergence. Fill **decision**, **points**, **notes**.

- pipeline decision: `match` | `system_error` | `participant_divergence`
- baseline decision: `match` | `model_error` | `adapter_valid_ok`
- points: `1` if awarded per the scoring mode, else `0`
  (Adapter-valid: 1 if the value is catalogue-valid, regardless of GT; Exact: 1 only on exact match; Semantic: 1 if semantically equivalent)

## base-S1-s01  (scenario 1, baseline)
_classified params: 8 | auto-matched (awarded 1): 7 | to judge below: 1_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] spreadsheet.append.google_sheets | `columns` | differ | Exact | {"message":"{{email_object.body}}"} | {"message":"{{$json.body}}"} | (single-shot; no dialogue) |  |  |

## base-S1-s02  (scenario 1, baseline)
_classified params: 8 | auto-matched (awarded 1): 7 | to judge below: 1_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] spreadsheet.append.google_sheets | `columns` | differ | Exact | {"message":"{{email_object.body}}"} | {"message":"{{$json.body}}"} | (single-shot; no dialogue) |  |  |

## base-S1-s03  (scenario 1, baseline)
_classified params: 8 | auto-matched (awarded 1): 7 | to judge below: 1_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] spreadsheet.append.google_sheets | `columns` | differ | Exact | {"message":"{{email_object.body}}"} | {"message":"{{$json.body}}"} | (single-shot; no dialogue) |  |  |

## P1-S1  (scenario 1, pipeline)
_classified params: 8 | auto-matched (awarded 1): 4 | to judge below: 4_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `filters` | differ | Exact | {} | {"subject":"dorpshuis"} | [configuring_parameters] Q: "Do you want to save every new email, or only emails that matc… |  |  |
| trigger | `pollTimes` | differ | Adapter-valid | everyMinute | everyHour | [configuring_parameters] Q: "How often would you like Gmail to check for new emails — ever… |  |  |
| step[0] spreadsheet.append.google_sheets | `columns` | differ | Exact | {"message":"{{email_object.body}}"} | {"message":"{{email_object.subject}}\n\n | [configuring_parameters] Q: "Which column in your Sheet 1 should each matching email be ad… |  |  |
| step[0] spreadsheet.append.google_sheets | `sheetName` | differ | Exact | Sheet1 | Sheet 1 | [configuring_parameters] Q: "Should I use “Sheet1” as the tab name, or did you mean “Sheet… |  |  |

## P3-S1  (scenario 1, pipeline)
_classified params: 8 | auto-matched (awarded 1): 6 | to judge below: 2_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyMinute | custom | [configuring_parameters] Q: "How often would you like Gmail to check for new emails — ever… |  |  |
| step[0] spreadsheet.append.google_sheets | `sheetName` | differ | Exact | Sheet1 | sheet1 | [configuring_parameters] Q: "Should I use Sheet1 as the tab name, or would you like to cha… |  |  |

## base-S2-s01  (scenario 2, baseline)
_classified params: 10 | auto-matched (awarded 1): 6 | to judge below: 4_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | (single-shot; no dialogue) |  |  |
| step[0] database.create_record.notion | `propertiesUi` | differ | Exact | {"Sender":"{{email_object.from}}","Email | {"Sender":"{{$json.from}}","Email Body": | (single-shot; no dialogue) |  |  |
| step[1] email.send.gmail | `message` | differ | Semantic | The CEO of Acme Corp {{record_object.pro | The CEO of Acme Corp has reached out fro | (single-shot; no dialogue) |  |  |
| step[1] email.send.gmail | `subject` | differ | Semantic | Respond to Customer | Please respond to Customer | (single-shot; no dialogue) |  |  |

## base-S2-s02  (scenario 2, baseline)
_classified params: 10 | auto-matched (awarded 1): 6 | to judge below: 4_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | (single-shot; no dialogue) |  |  |
| step[0] database.create_record.notion | `propertiesUi` | differ | Exact | {"Sender":"{{email_object.from}}","Email | {"Sender":"{{$json.from}}","Email Body": | (single-shot; no dialogue) |  |  |
| step[1] email.send.gmail | `message` | differ | Semantic | The CEO of Acme Corp {{record_object.pro | The CEO of Acme Corp has reached out fro | (single-shot; no dialogue) |  |  |
| step[1] email.send.gmail | `subject` | differ | Semantic | Respond to Customer | Please respond to Customer | (single-shot; no dialogue) |  |  |

## base-S2-s03  (scenario 2, baseline)
_classified params: 10 | auto-matched (awarded 1): 6 | to judge below: 4_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | (single-shot; no dialogue) |  |  |
| step[0] database.create_record.notion | `propertiesUi` | differ | Exact | {"Sender":"{{email_object.from}}","Email | {"Sender":"{{$json.from}}","Email Body": | (single-shot; no dialogue) |  |  |
| step[1] email.send.gmail | `message` | differ | Semantic | The CEO of Acme Corp {{record_object.pro | The CEO of Acme Corp has reached out fro | (single-shot; no dialogue) |  |  |
| step[1] email.send.gmail | `subject` | differ | Semantic | Respond to Customer | Please respond to Customer | (single-shot; no dialogue) |  |  |

## P1-S2  (scenario 2, pipeline)
_classified params: 10 | auto-matched (awarded 1): 7 | to judge below: 3_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] database.create_record.notion | `title` | differ | Exact | Mail | {{email_object.subject}} | [configuring_parameters] Q: "Should I use the email’s subject line as the title for the ne… |  |  |
| step[1] email.send.gmail | `message` | differ | Semantic | The CEO of Acme Corp {{record_object.pro | The CEO of Acme Corp has reached out fro | [configuring_parameters] Q: "Should the email message be: “The CEO of Acme Corp has reache… |  |  |
| step[1] email.send.gmail | `subject` | differ | Semantic | Respond to Customer | please respond to customer | [configuring_parameters] Q: "Should the subject line be “please respond to customer”, or w… |  |  |

## P3-S2  (scenario 2, pipeline)
_classified params: 10 | auto-matched (awarded 1): 5 | to judge below: 5_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | custom | [configuring_parameters] Q: "How often would you like Gmail to check for new emails from c… |  |  |
| step[0] database.create_record.notion | `propertiesUi` | differ | Exact | {"Sender":"{{email_object.from}}","Email | {"Sender":"{{email_object.from}}","Email | [configuring_parameters] Q: "What details should I save in the new Notion entry, and which… |  |  |
| step[0] database.create_record.notion | `title` | differ | Exact | Mail | {{email_object.subject}} | [configuring_parameters] Q: "Should I use the email’s subject line as the title for the ne… |  |  |
| step[1] email.send.gmail | `message` | differ | Semantic | The CEO of Acme Corp {{record_object.pro | the CEO of Acme corp has reached out fro | [configuring_parameters] Q: "Should the email message be: “the CEO of Acme corp has reache… |  |  |
| step[1] email.send.gmail | `subject` | differ | Semantic | Respond to Customer | please respond to customer | [configuring_parameters] Q: "Should the subject line be “please respond to customer”, or w… |  |  |

## base-S3-s01  (scenario 3, baseline)
_classified params: 10 | auto-matched (awarded 1): 7 | to judge below: 3_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | (single-shot; no dialogue) |  |  |
| step[0] email.send.gmail | `message` | differ | Semantic | An expense has been added to the expense | An expense has been added to the expense | (single-shot; no dialogue) |  |  |
| step[1] email.label.gmail | `messageId` | differ | Exact | {{send_result.messageId}} | {{$json.messageId}} | (single-shot; no dialogue) |  |  |

## base-S3-s02  (scenario 3, baseline)
_classified params: 10 | auto-matched (awarded 1): 7 | to judge below: 3_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | (single-shot; no dialogue) |  |  |
| step[0] email.send.gmail | `message` | differ | Semantic | An expense has been added to the expense | An expense has been added to the expense | (single-shot; no dialogue) |  |  |
| step[1] email.label.gmail | `messageId` | differ | Exact | {{send_result.messageId}} | {{$json.messageId}} | (single-shot; no dialogue) |  |  |

## base-S3-s03  (scenario 3, baseline)
_classified params: 10 | auto-matched (awarded 1): 7 | to judge below: 3_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | (single-shot; no dialogue) |  |  |
| step[0] email.send.gmail | `message` | differ | Semantic | An expense has been added to the expense | An expense has been added to the expense | (single-shot; no dialogue) |  |  |
| step[1] email.label.gmail | `messageId` | differ | Exact | {{send_result.messageId}} | {{$json.messageId}} | (single-shot; no dialogue) |  |  |

## P2-S3  (scenario 3, pipeline)
_classified params: 10 | auto-matched (awarded 1): 6 | to judge below: 4_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | custom | [configuring_parameters] Q: "How often would you like us to check your Google Sheet for ne… |  |  |
| step[0] email.send.gmail | `message` | differ | Semantic | An expense has been added to the expense | {{row_object.row['Expense name']}}
{{row | [configuring_parameters] Q: "Should I use the details from the new expense row in your she… |  |  |
| step[0] email.send.gmail | `subject` | differ | Semantic | Expense added | expense added | [configuring_parameters] Q: "Should the subject line be “expense added”, or would you like… |  |  |
| step[1] email.label.gmail | `labelIds` | differ | Exact | ["Label_expenses"] | ["[\"Label_expenses\"]"] | [configuring_parameters] Q: "Should I apply the Gmail label ID ['Label_expenses'] to the s… |  |  |

## P4-S3  (scenario 3, pipeline)
_classified params: 10 | auto-matched (awarded 1): 7 | to judge below: 3_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyHour | everyMinute | [configuring_parameters] Q: "How often would you like me to check the Expenses sheet for n… |  |  |
| step[0] email.send.gmail | `message` | differ | Semantic | An expense has been added to the expense | An expense has been added to the expense | [configuring_parameters] Q: "Should the email message be: “An expense has been added to th… |  |  |
| step[1] email.label.gmail | `labelIds` | differ | Exact | ["Label_expenses"] | ["[\"Label_expenses\"]"] | [configuring_parameters] Q: "Should I apply the Gmail label ID ['Label_expenses'] to the s… |  |  |

## base-S4-s01  (scenario 4, baseline)
_classified params: 8 | auto-matched (awarded 1): 7 | to judge below: 1_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] email.send.gmail | `message` | differ | Semantic | Dear finance department, 

Something wit | Dear finance department, a new file was  | (single-shot; no dialogue) |  |  |

## base-S4-s02  (scenario 4, baseline)
_classified params: 8 | auto-matched (awarded 1): 7 | to judge below: 1_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] email.send.gmail | `message` | differ | Semantic | Dear finance department, 

Something wit | Dear finance department, a new file was  | (single-shot; no dialogue) |  |  |

## base-S4-s03  (scenario 4, baseline)
_classified params: 8 | auto-matched (awarded 1): 7 | to judge below: 1_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] email.send.gmail | `message` | differ | Semantic | Dear finance department, 

Something wit | Dear finance department, a new file was  | (single-shot; no dialogue) |  |  |

## P2-S4  (scenario 4, pipeline)
_classified params: 8 | auto-matched (awarded 1): 5 | to judge below: 3_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| trigger | `pollTimes` | differ | Adapter-valid | everyMinute | everyHour | [configuring_parameters] Q: "How often would you like me to check the "guidelines" folder … |  |  |
| step[0] email.send.gmail | `message` | differ | Semantic | Dear finance department, 

Something wit | Link to the file: {{file_object.webViewL | [configuring_parameters] Q: "Should the email message say: “A new file was added to the gu… |  |  |
| step[0] email.send.gmail | `subject` | differ | Semantic | Guideline added | guidline added | [configuring_parameters] Q: "Should the email subject be "guidline added", or would you li… |  |  |

## P4-S4  (scenario 4, pipeline)
_classified params: 8 | auto-matched (awarded 1): 6 | to judge below: 2_

| location | element | status | mode | ground_truth | candidate | evidence | decision | pts |
|---|---|---|---|---|---|---|---|---|
| step[0] email.send.gmail | `message` | differ | Semantic | Dear finance department, 

Something wit | Dear finance department, a new file was  | [configuring_parameters] Q: "Should the email body be: “Dear finance department, a new fil… |  |  |
| step[0] email.send.gmail | `subject` | differ | Semantic | Guideline added | guideline added | [configuring_parameters] Q: "Should the subject line be "guideline added", or would you li… |  |  |
