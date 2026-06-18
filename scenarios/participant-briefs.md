# Participant Scenario Briefs

## General instructions (read aloud or hand to participant before starting)

> You are going to describe a workflow automation to an AI assistant in plain language — as if you were explaining it to a colleague. The assistant will ask you follow-up questions to clarify the details. Just answer as naturally as you can; there are no wrong answers.
>
> You will be given a scenario card. Read it carefully and then describe the automation to the assistant in your own words. You do not need to memorise every detail — you can refer back to the card at any time.
>
> If the assistant asks about a detail that is not described on your card, just choose whatever makes sense to you. There is no wrong answer.

---

## Scenario 1 — Email to Spreadsheet

**Scenario card (hand to participant):**

> You work at a company and receive emails in Gmail. You want to automatically save the body of every new email to a Google Spreadsheet called **Messages**, in a tab named **Sheet1**. The email body only should end up in the **message** column as a new row.
>
> *If the assistant asks about a detail that is not described on this card, just choose whatever makes sense to you. There is no wrong answer.*

---

## Scenario 2 — Customer Email → Notion → Colleague Alert

**Scenario card (hand to participant):**

> You receive emails in your Gmail from an important customer at **ceo@acmecorp.com**. Whenever one arrives, you want two things to happen:
>
> 1. Save that email to your Notion database called **Email Tracker** — by storing the sender's email address and the email body. The database item can be named **"Mail"**.
> 2. Then send an email to your colleague at **erik@fiber.nl** with the subject **"Please respond to Customer"** and a message saying:
>    *"The CEO of Acme Corp has reached out from their email address. I would like you to respond to them."* (the message should include the sender's email address).
>
> *If the assistant asks about a detail that is not described on this card, just choose whatever makes sense to you. There is no wrong answer.*

---

## Scenario 3 — Expense Sheet → Finance Email → Label

**Scenario card (hand to participant):**

> You manage expenses in a Google Sheet called **Expenses**. It has three columns: **Expense name**, **Expense amount**, and **Expense date**. Whenever a new expense row is added, you want an email sent from Gmail to **expenses@acmecorp.com** with the subject **"Expense added"** and a message saying:
>
> *"An expense has been added to the expenses sheet. The name is the expense name, the amount is the expense amount, and the date is the expense date."* (the message should include the actual name, amount, and date from the new row that was added to the google sheet).
>
> After sending, the email should be labelled in Gmail. You have a label called **Expenses** — its label ID is **Label_expenses**.
>
> *If the assistant asks about a detail that is not described on this card, just choose whatever makes sense to you. There is no wrong answer.*

---

## Scenario 4 — New Drive File to Finance Email

**Scenario card (hand to participant):**

> You have a Google Drive folder called **Guidelines**. Whenever a new file is added to it, you want an email sent from Gmail to **finance@acmecorp.com** with the subject **"Guideline added"** and a message saying:
>
> *"Dear finance department, a new file was added to the guideline folder. Please check whether this is correct."* (the message should include the file's name and a link to the file).
>
> *If the assistant asks about a detail that is not described on this card, just choose whatever makes sense to you. There is no wrong answer.*
