-- Phase 7, step a: the Prospect table.
--
-- Clause 4.5.15.  Prospect.csv is a bought-in list of potential customers
-- from an external data provider -- some are already brokerage customers,
-- most are not.  24,970 rows at SF=5.
--
-- IT RUNS BEFORE DimCustomer, AND THAT IS NOT OPTIONAL
--
-- Clause 4.5.4.2: "The TaxRate and Prospect tables will be referenced in the
-- transformation."  DimCustomer pulls AgencyID, CreditRating, NetWorth and
-- MarketingNameplate from here.
--
-- But Prospect.IsCustomer depends on DimCustomer, so the two are circular.
-- The specification resolves it by splitting the work, and Clause 4.5.15.1
-- puts the second half last: IsCustomer is set "after all customer records in
-- the batch have been processed".  So:
--
--     08_prospect.sql             rows, demographics, nameplate   <- here
--     09_dimcustomer.sql          reads them
--     11_prospect_iscustomer.sql  reads DimCustomer
--
-- __STAGING__ and __BATCHDATE__ are substituted by the loader.

CREATE DATABASE IF NOT EXISTS tpcdi_stage;

-- Clause 2.2.2.12.  22 fields, comma separated, no header.  Text throughout
-- for the same reason as the other staging tables: a value that will not cast
-- is a parsing bug, and the cast is a more informative place to find it.
DROP TABLE IF EXISTS tpcdi_stage.stg_prospect;
CREATE TABLE tpcdi_stage.stg_prospect (
    AgencyID          VARCHAR(30) NOT NULL,
    LastName          VARCHAR(30),
    FirstName         VARCHAR(30),
    MiddleInitial     VARCHAR(1),
    Gender            VARCHAR(1),
    AddressLine1      VARCHAR(80),
    AddressLine2      VARCHAR(80),
    PostalCode        VARCHAR(12),
    City              VARCHAR(25),
    State             VARCHAR(20),
    Country           VARCHAR(24),
    Phone             VARCHAR(30),
    Income            VARCHAR(9),
    NumberCars        VARCHAR(2),
    NumberChildren    VARCHAR(2),
    MaritalStatus     VARCHAR(1),
    Age               VARCHAR(3),
    CreditRating      VARCHAR(4),
    OwnOrRentFlag     VARCHAR(1),
    Employer          VARCHAR(30),
    NumberCreditCards VARCHAR(2),
    NetWorth          VARCHAR(12),
    PRIMARY KEY (AgencyID),
    -- DimCustomer matches on these five, upper-cased (Clause 4.5.4.3).
    KEY ix_match (LastName, FirstName, AddressLine1, PostalCode)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- CRLF, as everywhere.  An empty field becomes NULL rather than '': unlike
-- CustomerMgmt.xml there is no absent-versus-empty distinction to preserve
-- here, because every row carries every column.
LOAD DATA INFILE '__STAGING__/Prospect.csv'
  INTO TABLE tpcdi_stage.stg_prospect
  FIELDS TERMINATED BY ','
  LINES TERMINATED BY '\r\n'
  (@AgencyID, @LastName, @FirstName, @MiddleInitial, @Gender, @AddressLine1,
   @AddressLine2, @PostalCode, @City, @State, @Country, @Phone, @Income,
   @NumberCars, @NumberChildren, @MaritalStatus, @Age, @CreditRating,
   @OwnOrRentFlag, @Employer, @NumberCreditCards, @NetWorth)
  SET AgencyID          = @AgencyID,
      LastName          = nullif(@LastName, ''),
      FirstName         = nullif(@FirstName, ''),
      MiddleInitial     = nullif(@MiddleInitial, ''),
      Gender            = nullif(@Gender, ''),
      AddressLine1      = nullif(@AddressLine1, ''),
      AddressLine2      = nullif(@AddressLine2, ''),
      PostalCode        = nullif(@PostalCode, ''),
      City              = nullif(@City, ''),
      State             = nullif(@State, ''),
      Country           = nullif(@Country, ''),
      Phone             = nullif(@Phone, ''),
      Income            = nullif(@Income, ''),
      NumberCars        = nullif(@NumberCars, ''),
      NumberChildren    = nullif(@NumberChildren, ''),
      MaritalStatus     = nullif(@MaritalStatus, ''),
      Age               = nullif(@Age, ''),
      CreditRating      = nullif(@CreditRating, ''),
      OwnOrRentFlag     = nullif(@OwnOrRentFlag, ''),
      Employer          = nullif(@Employer, ''),
      NumberCreditCards = nullif(@NumberCreditCards, ''),
      NetWorth          = nullif(@NetWorth, '');

-- Clause 4.5.15.1.  IsCustomer is written as 0 here and corrected in
-- 11_prospect_iscustomer.sql once DimCustomer exists; the column is NOT NULL
-- so it cannot simply be left out.
INSERT INTO Prospect
    (AgencyID, SK_RecordDateID, SK_UpdateDateID, BatchID, IsCustomer,
     LastName, FirstName, MiddleInitial, Gender, AddressLine1, AddressLine2,
     PostalCode, City, State, Country, Phone, Income, NumberCars,
     NumberChildren, MaritalStatus, Age, CreditRating, OwnOrRentFlag,
     Employer, NumberCreditCards, NetWorth, MarketingNameplate)
SELECT
    p.AgencyID,
    -- Both date keys are the Batch Date for the historical load.
    (SELECT SK_DateID FROM DimDate WHERE DateValue = '__BATCHDATE__'),
    (SELECT SK_DateID FROM DimDate WHERE DateValue = '__BATCHDATE__'),
    1,
    0,
    p.LastName, p.FirstName, p.MiddleInitial,
    p.Gender, p.AddressLine1, p.AddressLine2, p.PostalCode, p.City, p.State,
    p.Country, p.Phone,
    CAST(p.Income            AS UNSIGNED),
    CAST(p.NumberCars        AS UNSIGNED),
    CAST(p.NumberChildren    AS UNSIGNED),
    p.MaritalStatus,
    CAST(p.Age               AS UNSIGNED),
    CAST(p.CreditRating      AS UNSIGNED),
    p.OwnOrRentFlag,
    p.Employer,
    CAST(p.NumberCreditCards AS UNSIGNED),
    CAST(p.NetWorth          AS UNSIGNED),
    -- Clause 4.5.15.1.  Zero or more tags joined with '+', NULL if none
    -- apply, and the order is fixed: Boomer+Spender, never Spender+Boomer.
    -- CONCAT_WS skips NULL arguments, which is exactly the behaviour wanted;
    -- it returns '' rather than NULL when every tag is absent, hence the
    -- outer NULLIF.
    NULLIF(CONCAT_WS('+',
        CASE WHEN CAST(p.NetWorth AS UNSIGNED) > 1000000
               OR CAST(p.Income   AS UNSIGNED) >  200000 THEN 'HighValue' END,
        CASE WHEN CAST(p.NumberChildren    AS UNSIGNED) > 3
               OR CAST(p.NumberCreditCards AS UNSIGNED) > 5 THEN 'Expenses' END,
        CASE WHEN CAST(p.Age AS UNSIGNED) > 45 THEN 'Boomer' END,
        CASE WHEN CAST(p.Income       AS UNSIGNED) <  50000
               OR CAST(p.CreditRating AS UNSIGNED) <    600
               OR CAST(p.NetWorth     AS UNSIGNED) < 100000 THEN 'MoneyAlert' END,
        CASE WHEN CAST(p.NumberCars        AS UNSIGNED) > 3
               OR CAST(p.NumberCreditCards AS UNSIGNED) > 7 THEN 'Spender' END,
        CASE WHEN CAST(p.Age      AS UNSIGNED) <      25
              AND CAST(p.NetWorth AS UNSIGNED) > 1000000 THEN 'Inherited' END
    ), '')
FROM tpcdi_stage.stg_prospect p;

-- The five fields DimCustomer and 11_prospect_iscustomer.sql match on.
-- Without it both joins are a block-nested loop over 10,388 x 24,160 rows,
-- which measured 129 s and 157 s; with it they are ref lookups at 0.14 s.
-- Clause 3.1.1 leaves indexing to the implementation, 7.2.3.2 permits
-- auxiliary structures.
CREATE INDEX IF NOT EXISTS ix_prospect_match
    ON Prospect (LastName, FirstName, AddressLine1, AddressLine2, PostalCode);

-- Clause 4.5.15.2: a Status message with the number of rows created.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'Prospect', 'Inserted rows', 'Status',
       CAST(count(*) AS char)
FROM Prospect;

-- END OF PIPELINE SQL
-- Nothing below this line is executed.  Ad-hoc queries are welcome here;
-- remember to qualify names with tpcdi_stage. or tpcdi. as appropriate.
