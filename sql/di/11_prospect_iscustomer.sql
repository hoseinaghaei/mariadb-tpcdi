-- Phase 7, step d: Prospect.IsCustomer.
--
-- Clause 4.5.15.1: IsCustomer is "set to True or False depending on whether
-- the prospective customer record matches a current customer record in
-- DimCustomer whose status is 'ACTIVE' after all customer records in the
-- batch have been processed."
--
-- This is the second half of the Prospect/DimCustomer cycle, and the reason
-- the whole of it lives inside one phase: split across a phase boundary it
-- would leave a half-built Prospect that no check could validate.
--
-- The match is the same five fields as the demographic lookup in
-- 09_dimcustomer.sql -- Clause 4.5.15.1 names FirstName, LastName,
-- AddressLine1, AddressLine2 and PostalCode.
--
-- WHY A JOIN AND NOT A CORRELATED EXISTS
--
-- The obvious form is `SET IsCustomer = EXISTS (SELECT 1 FROM DimCustomer
-- WHERE ...)`, and it took 133 seconds.  That subquery runs once per
-- Prospect row and scans DimCustomer each time: ix_prospect_match is on
-- Prospect and does nothing for a lookup going the other way.
--
-- Driving from DimCustomer instead -- 7,800 current active rows rather than
-- 24,970 prospects -- lets each one find its prospect through that index.
-- Two statements rather than one, because a join cannot set the false case.
--
-- <=> rather than UPPER(COALESCE(...)): the columns collate as
-- utf8mb4_uca1400_ai_ci so = is already case- and accent-insensitive, and
-- both functions would make the predicate non-sargable.  <=> is NULL-safe,
-- which AddressLine2 needs on both sides, and indexable.

UPDATE Prospect SET IsCustomer = 0;

UPDATE Prospect p
  JOIN DimCustomer c
    ON c.LastName     <=> p.LastName
   AND c.FirstName    <=> p.FirstName
   AND c.AddressLine1 <=> p.AddressLine1
   AND c.AddressLine2 <=> p.AddressLine2
   AND c.PostalCode   <=> p.PostalCode
SET p.IsCustomer = 1
WHERE c.IsCurrent = 1
  AND c.Status = 'ACTIVE';

-- END OF PIPELINE SQL
-- Nothing below this line is executed.  Ad-hoc queries are welcome here.
