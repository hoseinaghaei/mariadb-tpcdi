-- Phase 7, part 2: build the account event stream.
--
-- The XML names an account in NEW, ADDACCT, UPDACCT and CLOSEACCT.  It does
-- not name one in UPDCUST or INACT, yet both change accounts:
--
--   Clause 4.5.4.3, UPDCUST: "A history-tracking update to all current
--   accounts for this customer in the DimAccount table is also required"
--   Clause 4.5.4.3, INACT:   "All current accounts for this customer must
--   also be made inactive"
--
-- So those events have to be derived.  They are unioned with the explicit
-- ones here, before any dating happens, so the transform sees one ordered
-- stream per account rather than two sources it has to interleave.
--
-- WHY THIS IS NOT CIRCULAR
--
-- Knowing which accounts a customer has open at a given moment sounds like
-- it needs the account history, which is what we are building.  It does not:
-- UPDCUST and INACT only *modify* an account, never open or close one.  An
-- account's lifespan is therefore fixed by NEW, ADDACCT and CLOSEACCT alone,
-- all of which name their account explicitly.  Compute the lifespans first,
-- then a range join says which were open when.
--
-- One statement, not a loop and not a chunked series.  Measured at SF=5:
-- 0.40 s, 26,570 rows, 7 internal temp tables and none of them on disk.
-- Chunking it from the client would mean many round trips, repeated joins
-- and no reduction in the server's working set.  The counter worth watching
-- is Created_tmp_disk_tables: while it stays at zero nothing is spilling.

TRUNCATE tpcdi_stage.stg_cmg_account_event;

INSERT INTO tpcdi_stage.stg_cmg_account_event
    (seq, ActionType, ActionTS, C_ID, CA_ID, CA_TAX_ST, CA_B_ID, CA_NAME, explicit)

-- 1. What the XML actually said about an account.
SELECT seq, ActionType, ActionTS, C_ID, CA_ID, CA_TAX_ST, CA_B_ID, CA_NAME, 1
FROM tpcdi_stage.stg_cmg_account

UNION ALL

-- 2. The cascade.  CA_TAX_ST, CA_B_ID and CA_NAME are NULL because the
--    action said nothing about them -- the transform carries them forward,
--    which is the same absent-means-unchanged rule one level up.  `explicit`
--    = 0 is what lets it tell that apart from a supplied value.
SELECT a.seq, a.ActionType, a.ActionTS, a.C_ID, life.CA_ID,
       NULL, NULL, NULL, 0
FROM tpcdi_stage.stg_cmg_customer a
JOIN (
    SELECT CA_ID, C_ID,
           MIN(CASE WHEN ActionType IN ('NEW','ADDACCT') THEN seq END) AS opened,
           MIN(CASE WHEN ActionType  = 'CLOSEACCT'       THEN seq END) AS closed
    FROM tpcdi_stage.stg_cmg_account
    GROUP BY CA_ID, C_ID
) life
  ON life.C_ID = a.C_ID
 AND a.seq >  life.opened
 -- 4294967295 is INT UNSIGNED's maximum, standing in for "never closed".
 -- A cascade at exactly the closing action does not apply: the account is
 -- no longer current at that point.
 AND a.seq <  COALESCE(life.closed, 4294967295)
WHERE a.ActionType IN ('UPDCUST','INACT');
