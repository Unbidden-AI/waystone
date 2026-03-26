# meeting_notes / sprint_planning

Nadia: Alright team, let's kick off our Sprint 14 planning. First, a quick look back at Sprint 13. We aimed for 5 stories, and we completed 3.
Tom: That's right, Nadia. The user profile update and the basic KYC document upload features are both done and passed initial QA. Fatima did a great job on those.
Fatima: Thanks, Tom. The third one, the new merchant onboarding flow, is also complete. It's currently in Chris's queue for final regression.
Chris: Confirmed, Fatima. I'll get to that this afternoon. The two stories that didn't make it were the payment gateway API integration for Stripe Connect and the enhanced transaction logging.
Tom: The Stripe Connect integration hit a major snag. We're waiting on their enterprise support team to clarify some webhook authentication requirements. It's been a blocker for three days now.
Nadia: Understood. So, the payment gateway API is still blocked externally. We'll keep that on the backlog for now. Let's talk about Sprint 14 capacity. Tom, Fatima, any PTO or other commitments?
Tom: I'm good for the full sprint.
Fatima: Same here. No PTO planned.
Chris: I have a half-day next Friday for a dentist appointment, but otherwise full capacity.
Nadia: Great. So, relatively full capacity. Based on our average velocity of 18-20 story points, we should aim for a similar load. Now, for prioritization. We have two major features vying for attention: the transaction history export to CSV/PDF, and the improved real-time fraud alerts.
Tom: From an engineering perspective, the transaction history export is probably a bit lighter. We have most of the data models in place. It's mainly about aggregation and formatting. I'd estimate it at 8 story points.
Fatima: I agree with Tom. The fraud alerts, while critical, involve integrating with our new anomaly detection microservice and potentially some machine learning model updates. That's a solid 13-point story, maybe even 15 if we include the UI for alert management.
Chris: From a QA perspective, the export feature is straightforward to test. Fraud alerts, however, will require extensive scenario testing, edge cases, and performance checks. It's a much heavier lift for me.
Nadia: Okay, so transaction history export is lighter, fraud alerts are heavier but high impact. My product goal for Q3 is to enhance user self-service capabilities. The export feature directly supports that. Fraud alerts are more about risk mitigation, which is always important, but the current system is stable.
Nadia: What about the mobile push notifications for payment confirmations? That was also on our radar.
Tom: That one has a dependency on the new notification service being deployed to production, which is still in staging. I'd recommend deferring it.
Nadia: Good point, Tom. Let's defer mobile push notifications until Sprint 15, then. Given the capacity and product goals, I think we should prioritize the transaction history export for Sprint 14.
Fatima: Sounds good to me. I can start pulling the relevant data schemas together.
Tom: I'll create the main story and sub-tasks in Jira for the export feature. We can target 8 points for that.
Chris: I'll start thinking about test cases for the export, focusing on data integrity and format variations.
Nadia: Excellent. So, Sprint 14 will focus primarily on the transaction history export. We'll also pull in a couple of smaller bug fixes from the backlog to fill out the sprint, aiming for around 18 points total. Tom, can you pick two high-priority bugs, maybe related to the recent KYC updates?
Tom: Will do, Nadia. I'll grab JIRA-789 (KYC document upload error) and JIRA-792 (AML flag display issue). They're both 3-pointers.
Nadia: Perfect. So, 8 points for export, 6 points for bugs. That leaves us with 4-6 points for any spillover or smaller tasks.
Nadia: One last thing: we're still waiting on the final UI mockups for the fraud alert dashboard. Do we have any updates on design resources for that?
Tom: I spoke with Sarah from the design team yesterday. She's swamped with the new onboarding flow redesign. She said she might be able to get us something by the end of next week, but no promises.
Fatima: That's a bit concerning. We'll need those designs before we can even properly estimate the fraud alert story for a future sprint.
Nadia: Agreed. Tom, can you follow up with Sarah again by end of day tomorrow? Let's get a firm commitment or at least an estimated delivery date. Action item for you: follow up with Sarah on fraud alert UI mockups, due EOD Friday.
Tom: Got it. I'll ping her.
Nadia: Chris, can you ensure the merchant onboarding flow regression is completed by end of day today?
Chris: Yes, Nadia. That's my top priority after this meeting.
Nadia: Great. Fatima, can you start drafting the technical design for the transaction history export by Wednesday?
Fatima: Absolutely. I'll have a draft ready for review.
Nadia: Fantastic. Any other questions or concerns before we wrap up Sprint 14 planning?
Tom: All clear on my end.
Nadia: Alright then, let's make it a productive sprint!
