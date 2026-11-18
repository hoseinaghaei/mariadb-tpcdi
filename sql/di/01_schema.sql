-- TPC-DI Data Warehouse schema for MariaDB.
--
-- Transcribed by hand from the specification, Clause 3.2 (sections 3.2.1 to
-- 3.2.19, plus DimTrade at 3.2.7.1).  Column names and nullability are the
-- spec's; only the physical types are ours, mapped as set out below.
--
-- Clause 3.4.5 allows columns in any order, but they are kept in the spec's
-- order here so the two can be read side by side.
--
-- TYPE MAPPING  (spec Table 3.2.1 base types, Table 3.2.2 meta-types)
--
--   Spec              MariaDB            Why
--   ----------------  -----------------  ----------------------------------
--   BOOLEAN           TINYINT(1)         Clause 8.2.1.2 allows any two
--                                        distinct values for true/false.
--   CHAR(n)           VARCHAR(n)         The spec defines CHAR(n) as "up to
--                                        n characters", which is VARCHAR's
--                                        semantics, not SQL CHAR's fixed
--                                        width.  Avoids padding a
--                                        VARCHAR(150) description to 150
--                                        bytes on every row.
--   DATE              DATE               MariaDB covers 1000-01-01 to
--                                        9999-12-31; the spec needs
--                                        1800-01-01 to 9999-12-31.
--   DATETIME          DATETIME(3)        Spec requires 1 ms precision.
--   TIME              TIME(3)            Spec requires 1 ms precision.
--   NUM(m,n) n>0      DECIMAL(m,n)       Exact.  Clause 3.2 forbids float.
--   SNUM(m,n) n>0     DECIMAL(m,n)       Signed.
--   NUM(m) n=0        smallest exact     See per-column notes; every choice
--   SNUM(m) n=0       integer type that  holds the full m-digit range.
--                     holds m digits
--
--   SK_T      = NUM(11)   -> BIGINT   (11 digits exceeds INT UNSIGNED)
--   IDENT_T   = NUM(11)   -> BIGINT
--   TRADE_T   = NUM(15)   -> BIGINT
--   S_COUNT_T = NUM(12)   -> BIGINT
--   S_PRICE_T = SNUM(8,2) -> DECIMAL(8,2)
--   VALUE_T   = SNUM(10,2)-> DECIMAL(10,2)
--   BALANCE_T = SNUM(12,2)-> DECIMAL(12,2)
--   FIN_AGG_T = SNUM(15,2)-> DECIMAL(15,2)
--
-- Note on BatchID: the spec types it NUM(5), so the range is 0-99999.
-- SMALLINT UNSIGNED stops at 65535 and would not hold it, so INT UNSIGNED is
-- used even though the benchmark only ever writes 0, 1, 2 and 3.
--
-- No indexes beyond primary keys are declared here.  Clause 3.1.1 says the
-- specification does not require any, and Clause 7.2.3.2 allows auxiliary
-- structures to be added later -- which is where we will measure them.

SET sql_mode = 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION,PIPES_AS_CONCAT';

-- ---------------------------------------------------------------- 3.2.1
CREATE TABLE DimAccount (
    SK_AccountID    BIGINT        NOT NULL,
    AccountID       BIGINT        NOT NULL,
    SK_BrokerID     BIGINT        NOT NULL,
    SK_CustomerID   BIGINT        NOT NULL,
    Status          VARCHAR(10)   NOT NULL,
    AccountDesc     VARCHAR(50)       NULL,
    TaxStatus       TINYINT UNSIGNED  NULL,          -- NUM(1), values 0,1,2
    IsCurrent       TINYINT(1)    NOT NULL,
    BatchID         INT UNSIGNED  NOT NULL,
    EffectiveDate   DATE          NOT NULL,
    EndDate         DATE          NOT NULL,
    PRIMARY KEY (SK_AccountID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.2
CREATE TABLE DimBroker (
    SK_BrokerID     BIGINT        NOT NULL,
    BrokerID        BIGINT        NOT NULL,
    ManagerID       BIGINT            NULL,
    FirstName       VARCHAR(50)   NOT NULL,
    LastName        VARCHAR(50)   NOT NULL,
    MiddleInitial   VARCHAR(1)        NULL,
    Branch          VARCHAR(50)       NULL,
    Office          VARCHAR(50)       NULL,
    Phone           VARCHAR(14)       NULL,
    IsCurrent       TINYINT(1)    NOT NULL,
    BatchID         INT UNSIGNED  NOT NULL,
    EffectiveDate   DATE          NOT NULL,
    EndDate         DATE          NOT NULL,
    PRIMARY KEY (SK_BrokerID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.3
CREATE TABLE DimCompany (
    SK_CompanyID    BIGINT        NOT NULL,
    CompanyID       BIGINT        NOT NULL,
    Status          VARCHAR(10)   NOT NULL,
    Name            VARCHAR(60)   NOT NULL,
    Industry        VARCHAR(50)   NOT NULL,
    SPrating        VARCHAR(4)        NULL,
    isLowGrade      TINYINT(1)        NULL,
    CEO             VARCHAR(100)  NOT NULL,
    AddressLine1    VARCHAR(80)       NULL,
    AddressLine2    VARCHAR(80)       NULL,
    PostalCode      VARCHAR(12)   NOT NULL,
    City            VARCHAR(25)   NOT NULL,
    StateProv       VARCHAR(20)   NOT NULL,
    Country         VARCHAR(24)       NULL,
    Description     VARCHAR(150)  NOT NULL,
    FoundingDate    DATE              NULL,
    IsCurrent       TINYINT(1)    NOT NULL,
    BatchID         INT UNSIGNED  NOT NULL,
    EffectiveDate   DATE          NOT NULL,
    EndDate         DATE          NOT NULL,
    PRIMARY KEY (SK_CompanyID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.4
CREATE TABLE DimCustomer (
    SK_CustomerID       BIGINT        NOT NULL,
    CustomerID          BIGINT        NOT NULL,
    TaxID               VARCHAR(20)   NOT NULL,
    Status              VARCHAR(10)   NOT NULL,
    LastName            VARCHAR(30)   NOT NULL,
    FirstName           VARCHAR(30)   NOT NULL,
    MiddleInitial       VARCHAR(1)        NULL,
    Gender              VARCHAR(1)        NULL,
    Tier                TINYINT UNSIGNED  NULL,      -- NUM(1)
    DOB                 DATE          NOT NULL,
    AddressLine1        VARCHAR(80)   NOT NULL,
    AddressLine2        VARCHAR(80)       NULL,
    PostalCode          VARCHAR(12)   NOT NULL,
    City                VARCHAR(25)   NOT NULL,
    StateProv           VARCHAR(20)   NOT NULL,
    Country             VARCHAR(24)       NULL,
    Phone1              VARCHAR(30)       NULL,
    Phone2              VARCHAR(30)       NULL,
    Phone3              VARCHAR(30)       NULL,
    Email1              VARCHAR(50)       NULL,
    Email2              VARCHAR(50)       NULL,
    NationalTaxRateDesc VARCHAR(50)       NULL,
    NationalTaxRate     DECIMAL(6,5)      NULL,
    LocalTaxRateDesc    VARCHAR(50)       NULL,
    LocalTaxRate        DECIMAL(6,5)      NULL,
    AgencyID            VARCHAR(30)       NULL,
    CreditRating        INT UNSIGNED      NULL,      -- NUM(5)
    NetWorth            BIGINT            NULL,      -- SNUM(10)
    MarketingNameplate  VARCHAR(100)      NULL,
    IsCurrent           TINYINT(1)    NOT NULL,
    BatchID             INT UNSIGNED  NOT NULL,
    EffectiveDate       DATE          NOT NULL,
    EndDate             DATE          NOT NULL,
    PRIMARY KEY (SK_CustomerID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.5
CREATE TABLE DimDate (
    SK_DateID         BIGINT            NOT NULL,
    DateValue         DATE              NOT NULL,
    DateDesc          VARCHAR(20)       NOT NULL,
    CalendarYearID    SMALLINT UNSIGNED NOT NULL,    -- NUM(4)
    CalendarYearDesc  VARCHAR(20)       NOT NULL,
    CalendarQtrID     INT UNSIGNED      NOT NULL,    -- NUM(5)
    CalendarQtrDesc   VARCHAR(20)       NOT NULL,
    CalendarMonthID   INT UNSIGNED      NOT NULL,    -- NUM(6)
    CalendarMonthDesc VARCHAR(20)       NOT NULL,
    CalendarWeekID    INT UNSIGNED      NOT NULL,    -- NUM(6)
    CalendarWeekDesc  VARCHAR(20)       NOT NULL,
    DayOfWeekNum      TINYINT UNSIGNED  NOT NULL,    -- NUM(1)
    DayOfWeekDesc     VARCHAR(10)       NOT NULL,
    FiscalYearID      SMALLINT UNSIGNED NOT NULL,    -- NUM(4)
    FiscalYearDesc    VARCHAR(20)       NOT NULL,
    FiscalQtrID       INT UNSIGNED      NOT NULL,    -- NUM(5)
    FiscalQtrDesc     VARCHAR(20)       NOT NULL,
    HolidayFlag       TINYINT(1)            NULL,
    PRIMARY KEY (SK_DateID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.6
CREATE TABLE DimSecurity (
    SK_SecurityID        BIGINT        NOT NULL,
    Symbol               VARCHAR(15)   NOT NULL,
    Issue                VARCHAR(6)    NOT NULL,
    Status               VARCHAR(10)   NOT NULL,
    Name                 VARCHAR(70)   NOT NULL,
    ExchangeID           VARCHAR(6)    NOT NULL,
    SK_CompanyID         BIGINT        NOT NULL,
    SharesOutstanding    BIGINT        NOT NULL,     -- S_COUNT_T NUM(12)
    FirstTrade           DATE          NOT NULL,
    FirstTradeOnExchange DATE          NOT NULL,
    Dividend             DECIMAL(10,2) NOT NULL,     -- VALUE_T
    IsCurrent            TINYINT(1)    NOT NULL,
    BatchID              INT UNSIGNED  NOT NULL,
    EffectiveDate        DATE          NOT NULL,
    EndDate              DATE          NOT NULL,
    PRIMARY KEY (SK_SecurityID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.7
CREATE TABLE DimTime (
    SK_TimeID       BIGINT            NOT NULL,
    TimeValue       TIME(3)           NOT NULL,
    HourID          TINYINT UNSIGNED  NOT NULL,      -- NUM(2)
    HourDesc        VARCHAR(20)       NOT NULL,
    MinuteID        TINYINT UNSIGNED  NOT NULL,
    MinuteDesc      VARCHAR(20)       NOT NULL,
    SecondID        TINYINT UNSIGNED  NOT NULL,
    SecondDesc      VARCHAR(20)       NOT NULL,
    MarketHoursFlag TINYINT(1)            NULL,
    OfficeHoursFlag TINYINT(1)            NULL,
    PRIMARY KEY (SK_TimeID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------------- 3.2.7.1
-- DimTrade is both dimension and fact, and is NOT history-tracking: it is
-- updated in place, so it has no IsCurrent/EffectiveDate/EndDate.
CREATE TABLE DimTrade (
    TradeID         BIGINT        NOT NULL,
    SK_BrokerID     BIGINT            NULL,
    SK_CreateDateID BIGINT        NOT NULL,
    SK_CreateTimeID BIGINT        NOT NULL,
    SK_CloseDateID  BIGINT            NULL,
    SK_CloseTimeID  BIGINT            NULL,
    Status          VARCHAR(10)   NOT NULL,
    Type            VARCHAR(12)   NOT NULL,
    CashFlag        TINYINT(1)    NOT NULL,
    SK_SecurityID   BIGINT        NOT NULL,
    SK_CompanyID    BIGINT        NOT NULL,
    Quantity        INT UNSIGNED  NOT NULL,          -- NUM(6,0)
    BidPrice        DECIMAL(8,2)  NOT NULL,
    SK_CustomerID   BIGINT        NOT NULL,
    SK_AccountID    BIGINT        NOT NULL,
    ExecutedBy      VARCHAR(64)   NOT NULL,
    TradePrice      DECIMAL(8,2)      NULL,
    Fee             DECIMAL(10,2)     NULL,
    Commission      DECIMAL(10,2)     NULL,
    Tax             DECIMAL(10,2)     NULL,
    BatchID         INT UNSIGNED  NOT NULL,
    PRIMARY KEY (TradeID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.8
-- No primary key: the spec defines none, and the same message legitimately
-- repeats.  Clause 7.5.1 reads phase timings out of the PCR rows here.
CREATE TABLE DImessages (
    MessageDateAndTime DATETIME(3)  NOT NULL,
    BatchID            INT UNSIGNED NOT NULL,
    MessageSource      VARCHAR(30)      NULL,
    MessageText        VARCHAR(50)  NOT NULL,
    MessageType        VARCHAR(12)  NOT NULL,
    MessageData        VARCHAR(100)     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 3.2.9
CREATE TABLE FactCashBalances (
    SK_CustomerID BIGINT        NOT NULL,
    SK_AccountID  BIGINT        NOT NULL,
    SK_DateID     BIGINT        NOT NULL,
    Cash          DECIMAL(15,2) NOT NULL,
    BatchID       INT UNSIGNED  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.10
CREATE TABLE FactHoldings (
    TradeID        BIGINT       NOT NULL,
    CurrentTradeID BIGINT       NOT NULL,
    SK_CustomerID  BIGINT       NOT NULL,
    SK_AccountID   BIGINT       NOT NULL,
    SK_SecurityID  BIGINT       NOT NULL,
    SK_CompanyID   BIGINT       NOT NULL,
    SK_DateID      BIGINT       NOT NULL,
    SK_TimeID      BIGINT       NOT NULL,
    CurrentPrice   DECIMAL(8,2)     NULL,            -- S_PRICE_T, >0
    CurrentHolding INT          NOT NULL,            -- SNUM(6), may be negative
    BatchID        INT UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.11
CREATE TABLE FactMarketHistory (
    SK_SecurityID            BIGINT        NOT NULL,
    SK_CompanyID             BIGINT        NOT NULL,
    SK_DateID                BIGINT        NOT NULL,
    PERatio                  DECIMAL(10,2)     NULL, -- NULL when no earnings
    Yield                    DECIMAL(5,2)  NOT NULL,
    FiftyTwoWeekHigh         DECIMAL(8,2)  NOT NULL,
    SK_FiftyTwoWeekHighDate  BIGINT        NOT NULL,
    FiftyTwoWeekLow          DECIMAL(8,2)  NOT NULL,
    SK_FiftyTwoWeekLowDate   BIGINT        NOT NULL,
    ClosePrice               DECIMAL(8,2)  NOT NULL,
    DayHigh                  DECIMAL(8,2)  NOT NULL,
    DayLow                   DECIMAL(8,2)  NOT NULL,
    Volume                   BIGINT        NOT NULL, -- NUM(12)
    BatchID                  INT UNSIGNED  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.12
CREATE TABLE FactWatches (
    SK_CustomerID         BIGINT       NOT NULL,
    SK_SecurityID         BIGINT       NOT NULL,
    SK_DateID_DatePlaced  BIGINT       NOT NULL,
    SK_DateID_DateRemoved BIGINT           NULL,
    BatchID               INT UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.13
CREATE TABLE Industry (
    IN_ID   VARCHAR(2)  NOT NULL,
    IN_NAME VARCHAR(50) NOT NULL,
    IN_SC_ID VARCHAR(4) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.14
-- SK_CompanyID is typed IDENT_T here, not SK_T, which is how the spec has
-- it (Table 3.2.17).  Same width either way.
CREATE TABLE Financial (
    SK_CompanyID      BIGINT        NOT NULL,
    FI_YEAR           SMALLINT UNSIGNED NOT NULL,    -- NUM(4)
    FI_QTR            TINYINT UNSIGNED  NOT NULL,    -- NUM(1), 1-4
    FI_QTR_START_DATE DATE          NOT NULL,
    FI_REVENUE        DECIMAL(15,2) NOT NULL,
    FI_NET_EARN       DECIMAL(15,2) NOT NULL,
    FI_BASIC_EPS      DECIMAL(10,2) NOT NULL,
    FI_DILUT_EPS      DECIMAL(10,2) NOT NULL,
    FI_MARGIN         DECIMAL(10,2) NOT NULL,
    FI_INVENTORY      DECIMAL(15,2) NOT NULL,
    FI_ASSETS         DECIMAL(15,2) NOT NULL,
    FI_LIABILITY      DECIMAL(15,2) NOT NULL,
    FI_OUT_BASIC      BIGINT        NOT NULL,        -- SNUM(12)
    FI_OUT_DILUT      BIGINT        NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.15
CREATE TABLE Prospect (
    AgencyID           VARCHAR(30)      NOT NULL,
    SK_RecordDateID    BIGINT           NOT NULL,
    SK_UpdateDateID    BIGINT           NOT NULL,
    BatchID            INT UNSIGNED     NOT NULL,
    IsCustomer         TINYINT(1)       NOT NULL,
    LastName           VARCHAR(30)      NOT NULL,
    FirstName          VARCHAR(30)      NOT NULL,
    MiddleInitial      VARCHAR(1)           NULL,
    Gender             VARCHAR(1)           NULL,
    AddressLine1       VARCHAR(80)          NULL,
    AddressLine2       VARCHAR(80)          NULL,
    PostalCode         VARCHAR(12)          NULL,
    City               VARCHAR(25)      NOT NULL,
    State              VARCHAR(20)      NOT NULL,
    Country            VARCHAR(24)          NULL,
    Phone              VARCHAR(30)          NULL,
    Income             INT UNSIGNED         NULL,    -- NUM(9)
    NumberCars         TINYINT UNSIGNED     NULL,    -- NUM(2)
    NumberChildren     TINYINT UNSIGNED     NULL,
    MaritalStatus      VARCHAR(1)           NULL,
    Age                SMALLINT UNSIGNED    NULL,    -- NUM(3), 999 > TINYINT
    CreditRating       SMALLINT UNSIGNED    NULL,    -- NUM(4)
    OwnOrRentFlag      VARCHAR(1)           NULL,
    Employer           VARCHAR(30)          NULL,
    NumberCreditCards  TINYINT UNSIGNED     NULL,
    NetWorth           BIGINT UNSIGNED      NULL,    -- NUM(12)
    MarketingNameplate VARCHAR(100)         NULL,
    PRIMARY KEY (AgencyID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.16
CREATE TABLE StatusType (
    ST_ID   VARCHAR(4)  NOT NULL,
    ST_NAME VARCHAR(10) NOT NULL,
    PRIMARY KEY (ST_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.17
CREATE TABLE TaxRate (
    TX_ID   VARCHAR(4)   NOT NULL,
    TX_NAME VARCHAR(50)  NOT NULL,
    TX_RATE DECIMAL(6,5) NOT NULL,
    PRIMARY KEY (TX_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.18
CREATE TABLE TradeType (
    TT_ID      VARCHAR(3)       NOT NULL,
    TT_NAME    VARCHAR(12)      NOT NULL,
    TT_IS_SELL TINYINT UNSIGNED NOT NULL,            -- NUM(1)
    TT_IS_MRKT TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (TT_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------- 3.2.19
-- `Date` is backticked: it is a reserved word in MariaDB.
CREATE TABLE Audit (
    DataSet   VARCHAR(20)   NOT NULL,
    BatchID   INT UNSIGNED      NULL,
    `Date`    DATE              NULL,
    Attribute VARCHAR(50)   NOT NULL,
    Value     BIGINT            NULL,                -- SNUM(15)
    DValue    DECIMAL(15,5)     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
