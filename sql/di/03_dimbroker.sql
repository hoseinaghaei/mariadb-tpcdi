-- Phase 5: DimBroker from HR.csv.
--
-- Clause 4.5.2.  HR.csv is a full extract of every employee; only those with
-- EmployeeJobCode 314 are brokers and the rest are ignored.  There is no CDC
-- and no history in the source -- it is a snapshot -- so although DimBroker is
-- a history-tracking dimension (Clause 4.4.1.1), nothing here expires a row.
--
-- __STAGING__ is replaced with the batch directory by the loader so one file
-- serves every scale factor.
--
-- WHY A STAGING TABLE, AND WHY IN ANOTHER DATABASE
--
-- LOAD DATA has no WHERE clause, so the job-code filter cannot be applied
-- while reading.  The file lands in a staging table and the filter runs as a
-- set-based INSERT ... SELECT.  Clause 4.3.7 permits auxiliary data
-- structures, and Clause 7.2.3.2 permits them in the warehouse itself -- but
-- putting them in the `tpcdi` schema would mean it no longer holds exactly
-- the twenty tables of Clause 3.2, which is something the Phase 3 checks
-- assert.  They live in `tpcdi_stage` instead, so the warehouse stays exactly
-- what the specification describes.

CREATE DATABASE IF NOT EXISTS tpcdi_stage;

DROP TABLE IF EXISTS tpcdi_stage.stg_hr;
CREATE TABLE tpcdi_stage.stg_hr (
    EmployeeID        BIGINT           NOT NULL,   -- IDENT_T
    ManagerID         BIGINT               NULL,
    EmployeeFirstName VARCHAR(30)      NOT NULL,
    EmployeeLastName  VARCHAR(30)      NOT NULL,
    EmployeeMI        VARCHAR(1)           NULL,
    EmployeeJobCode   SMALLINT UNSIGNED    NULL,   -- NUM(3)
    EmployeeBranch    VARCHAR(30)          NULL,
    EmployeeOffice    VARCHAR(10)          NULL,
    EmployeePhone     VARCHAR(14)          NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Clause 2.2.2.10.  No header row: the first line is data.  LINES TERMINATED
-- BY '\r\n' because the generator writes CRLF -- see 02_load_reference.sql.
-- Empty text fields become NULL rather than '': Clause 8.2.2.1 defines an
-- empty field as how NULL is written, so that is what an empty one means.
LOAD DATA INFILE '__STAGING__/HR.csv'
  INTO TABLE tpcdi_stage.stg_hr
  FIELDS TERMINATED BY ','
  LINES TERMINATED BY '\r\n'
  (EmployeeID, @ManagerID, EmployeeFirstName, EmployeeLastName, @EmployeeMI,
   @EmployeeJobCode, @EmployeeBranch, @EmployeeOffice, @EmployeePhone)
  SET ManagerID       = nullif(@ManagerID, ''),
      EmployeeMI      = nullif(@EmployeeMI, ''),
      EmployeeJobCode = nullif(@EmployeeJobCode, ''),
      EmployeeBranch  = nullif(@EmployeeBranch, ''),
      EmployeeOffice  = nullif(@EmployeeOffice, ''),
      EmployeePhone   = nullif(@EmployeePhone, '');

-- Clause 4.5.2.1:
--   * EmployeeJobCode 314 only; everything else is ignored.
--   * SK_BrokerID is a new unique surrogate key (4.4.1.3).  Clause 8.2.1.2
--     allows surrogate key VALUES to differ between implementations as long
--     as the key/foreign-key references are internally correct, so any
--     unique assignment is compliant.  ROW_NUMBER over BrokerID is used
--     because it is deterministic: the same input gives the same keys, which
--     makes two runs comparable.
--   * IsCurrent true, EndDate 9999-12-31 (4.4.1.2).
--   * EffectiveDate is the earliest date in DimDate, read from the table
--     rather than hard-coded, so it stays correct if the date range changes.
--   * BatchID 1: the Historical Load phase (4.4.2.1).
INSERT INTO DimBroker
    (SK_BrokerID, BrokerID, ManagerID, FirstName, LastName, MiddleInitial,
     Branch, Office, Phone, IsCurrent, BatchID, EffectiveDate, EndDate)
SELECT
    ROW_NUMBER() OVER (ORDER BY h.EmployeeID),
    h.EmployeeID,
    h.ManagerID,
    h.EmployeeFirstName,
    h.EmployeeLastName,
    h.EmployeeMI,
    h.EmployeeBranch,
    h.EmployeeOffice,
    h.EmployeePhone,
    1,
    1,
    (SELECT min(DateValue) FROM DimDate),
    '9999-12-31'
FROM tpcdi_stage.stg_hr AS h
WHERE h.EmployeeJobCode = 314;

-- Clause 3.2.8: a "Status" message gives information about DI processing.
-- The specification prescribes no particular message for DimBroker -- unlike,
-- say, FactMarketHistory's "No earnings for company" alert, whose exact text
-- the audit checks -- so this is ours, and uses MessageType 'Status', which
-- Clause 3.2.8.1 permits alongside the defined types.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'DimBroker', 'Inserted rows', 'Status',
       cast(count(*) AS char)
FROM DimBroker;

DROP TABLE IF EXISTS tpcdi_stage.stg_hr;
