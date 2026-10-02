# 2-Architecture

This folder has the architectural diagram of the Micro-Lending & Peer Credit Risk Assessor.

Files:
- Architectural_Diagram.png : the system architecture diagram.

About the diagram:
The system is split into three layers.
- Presentation layer : the borrower interface and the lender interface.
- Application layer : authentication and access control, borrower profile service, risk assessment engine, loan service, repayment scheduler, notification/warning service and the transaction manager (ACID).
- Data layer : the database that stores users, financial profiles, loans, repayment schedules and transactions.

Borrower and lender requests go through authentication first. The risk assessment engine uses the borrower profile to assign a Low, Medium or High risk tier, and the warning service is triggered for High risk. Repayments go through the transaction manager so they run as ACID transactions.
