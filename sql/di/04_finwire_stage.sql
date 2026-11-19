-- Phase 6, part 1: staging tables for the parsed FINWIRE records.
--
-- Columns are the FINWIRE field names, not the warehouse's, so that this half
-- of the pipeline can be read against Table 2.2.8 directly.  The mapping to
-- warehouse columns happens in 05_finwire_transform.sql.
--
-- Everything arrives as text except PTS, which the parser has already
-- reshaped into a datetime.  Numbers stay as strings here and are cast in the
-- transform: a value that will not cast is a parsing bug, and finding it at
-- the cast is more informative than finding it at the load.
--
-- In tpcdi_stage, not tpcdi, so the warehouse keeps exactly the twenty tables
-- of Clause 3.2 -- which the Phase 3 checks assert.

CREATE DATABASE IF NOT EXISTS tpcdi_stage;

DROP TABLE IF EXISTS tpcdi_stage.stg_fw_cmp;
CREATE TABLE tpcdi_stage.stg_fw_cmp (
    PTS           DATETIME     NOT NULL,
    CompanyName   VARCHAR(60),
    CIK           VARCHAR(10),
    Status        VARCHAR(4),
    IndustryID    VARCHAR(2),
    SPrating      VARCHAR(4),
    FoundingDate  VARCHAR(8),
    AddrLine1     VARCHAR(80),
    AddrLine2     VARCHAR(80),
    PostalCode    VARCHAR(12),
    City          VARCHAR(25),
    StateProvince VARCHAR(20),
    Country       VARCHAR(24),
    CEOname       VARCHAR(46),
    Description   VARCHAR(150),
    KEY (CIK, PTS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS tpcdi_stage.stg_fw_sec;
CREATE TABLE tpcdi_stage.stg_fw_sec (
    PTS             DATETIME   NOT NULL,
    Symbol          VARCHAR(15),
    IssueType       VARCHAR(6),
    Status          VARCHAR(4),
    Name            VARCHAR(70),
    ExID            VARCHAR(6),
    ShOut           VARCHAR(13),
    FirstTradeDate  VARCHAR(8),
    FirstTradeExchg VARCHAR(8),
    Dividend        VARCHAR(12),
    CoNameOrCIK     VARCHAR(60),
    KEY (Symbol, PTS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS tpcdi_stage.stg_fw_fin;
CREATE TABLE tpcdi_stage.stg_fw_fin (
    PTS          DATETIME   NOT NULL,
    `Year`       VARCHAR(4),
    Quarter      VARCHAR(1),
    QtrStartDate VARCHAR(8),
    PostingDate  VARCHAR(8),
    Revenue      VARCHAR(17),
    Earnings     VARCHAR(17),
    EPS          VARCHAR(12),
    DilutedEPS   VARCHAR(12),
    Margin       VARCHAR(12),
    Inventory    VARCHAR(17),
    Assets       VARCHAR(17),
    Liabilities  VARCHAR(17),
    ShOut        VARCHAR(13),
    DilutedShOut VARCHAR(13),
    CoNameOrCIK  VARCHAR(60),
    KEY (CoNameOrCIK, PTS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- END OF PIPELINE SQL
-- Nothing below this line is executed.  Ad-hoc queries are welcome here;
-- remember to qualify names with tpcdi_stage. or tpcdi. as appropriate.
