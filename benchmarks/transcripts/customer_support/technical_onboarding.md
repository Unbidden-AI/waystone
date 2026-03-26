# customer_support / technical_onboarding

Sam: Hi Jamie, thanks for joining. I'm Sam, your support engineer for today. How can I help you get started with your Slack SSO integration?

Jamie: Hi Sam, I'm Jamie, IT Admin at Acme Corp. We're looking to set up single sign-on for our 200-person Slack workspace, `acmecorp.slack.com`. We're currently using Okta as our identity provider.

Sam: Excellent. Okta is a common choice. Are you planning to use SAML 2.0 or OAuth 2.0 for the integration?

Jamie: That's actually one of my first questions. What's the recommended approach for an enterprise setup like ours? We want robust security and user provisioning.

Sam: For enterprise use cases, especially with Okta, SAML 2.0 is generally preferred. It offers more granular control over attributes, better support for Just-In-Time provisioning, and is the standard for many enterprise applications. OAuth 2.0 is great for API access and consumer apps, but SAML is usually the go-to for IdP-initiated and SP-initiated SSO.

Jamie: Okay, SAML 2.0 sounds like the way to go then. We're ready to start the configuration.

Sam: Great. So, the general flow will be: create a SAML application in Okta, configure it with details from Slack, then upload the Okta metadata to Slack. Ready to dive into your Okta admin console?

Jamie: Yep, I'm logged into `acmecorp.okta.com` right now. Where should I start?

Sam: First, navigate to Applications, then click "Create App Integration." Choose "SAML 2.0" as the sign-on method. Once you're in the SAML settings, you'll need to input some details from Slack.

Jamie: Okay, I've got the SAML 2.0 app creation wizard open. What details do I need from Slack?

Sam: In your Slack workspace, go to `Administration` > `Workspace settings` > `Authentication` > `Configure SAML`. You'll find the "ACS URL (Consumer Service URL)" and "SP Entity ID (Audience URI)" there. Copy those into the corresponding fields in Okta.

Jamie: Got it. I've copied `https://acmecorp.slack.com/sso/saml` for the ACS URL and `https://slack.com` for the SP Entity ID. What's next in Okta?

Sam: Now, you'll need to configure the attribute statements. For basic setup, ensure you're mapping `user.email` to `email` and `user.firstName` to `firstName`, `user.lastName` to `lastName`. Once that's done, save the application.

Jamie: Done. Email, first name, last name mapped. I've saved the app.

Sam: Perfect. Now, back in your Okta SAML application settings, go to the "Sign On" tab. You'll see a section called "SAML Signing Certificates." Download the "Identity Provider metadata" XML file. This is what we'll upload to Slack.

Jamie: Okay, I've downloaded `Okta_SAML_Metadata_acmecorp.xml`. Now I go back to Slack's SAML configuration page?

Sam: Exactly. On Slack's SAML configuration page, you should see an option to "Upload XML file." Go ahead and upload that `Okta_SAML_Metadata_acmecorp.xml` file you just downloaded.

Jamie: Uploading now... Okay, it says "Metadata uploaded successfully." Should I try testing the connection?

Sam: Yes, please do. There should be a "Test Configuration" button or similar on the Slack page.

Jamie: Clicking "Test Configuration"... Hmm, it's giving me an error. "SAML certificate mismatch. The certificate provided by your IdP does not match the one expected by Slack."

Sam: Ah, a common one. Did you download the *latest* metadata XML from Okta *after* saving the application? Sometimes, if you download it too early or if there's an old certificate cached, this can happen.

Jamie: You know what, I might have downloaded it before I finalized all the attribute mappings. Let me go back to Okta, re-download the metadata XML, and re-upload it to Slack.

Sam: Sounds like a plan. Make sure it's the most current one.

Jamie: Okay, re-downloaded, re-uploaded. Trying the "Test Configuration" again... Success! It says "SAML connection verified." That was quick.

Sam: Great! Now that the basic connection is working, let's talk about user provisioning. Do you want to enable SCIM for automatic user and group synchronization from Okta?

Jamie: Absolutely. We want to manage our 200 users and their group memberships directly from Okta. How do we set that up?

Sam: In your Okta SAML application, go to the "Provisioning" tab. Enable SCIM provisioning. You'll need to provide the SCIM connector base URL and a unique token from Slack. Slack will provide these under `Administration` > `Workspace settings` > `Authentication` > `Configure SAML` > `SCIM Provisioning`.

Jamie: Okay, I'm in Slack's SCIM settings. I see the SCIM Connector Base URL and a bearer token. I'm pasting those into Okta now. I'll also enable "Create Users," "Update User Attributes," and "Deactivate Users."

Sam: Perfect. And remember to set up your group push rules in Okta under the "Push Groups" tab if you want to sync specific Okta groups to Slack.

Jamie: Right, I'll set up a rule to push our "Acme All Employees" group and our "IT Department" group. One quick question: how does this affect guest accounts? We have about 15 external contractors.

Sam: Good question. Slack's guest accounts are typically managed directly within Slack and aren't usually provisioned via SCIM from your IdP. They're designed for limited access. Your internal users will use SSO, but guests will continue to log in with their Slack credentials or email magic links.

Jamie: Understood. So, guests remain separate. That makes sense.

Sam: Exactly. Now that the connection is verified and SCIM is configured, let's try logging in with a few actual test users. Can you try logging in as Alice Smith, Bob Johnson, and Carol White?

Jamie: Okay, I'm having Alice try to log in via `acmecorp.slack.com`... She's in! Bob just confirmed he's logged in too. And Carol just messaged me from Slack. All three test users are successfully authenticated via Okta.

Sam: Fantastic! That confirms the SAML SSO is fully operational for your Acme Corp workspace.

Jamie: That's brilliant, Sam. Thanks for your clear guidance. This was much smoother than I anticipated.

Sam: You're very welcome, Jamie. If you run into any further issues or have questions about advanced configurations, don't hesitate to reach out. We're here to help.
