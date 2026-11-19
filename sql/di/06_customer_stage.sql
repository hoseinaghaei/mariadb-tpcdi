-- Phase 7, part 1: staging tables for the parsed CustomerMgmt.xml actions.
--
-- Columns are the XML's own field names, not the warehouse's, so this half of
-- the pipeline reads against Clause 2.2.2.5 directly.  The mapping to
-- DimCustomer and DimAccount happens in 07_customer_transform.sql.
--
-- Widths are the source widths from Clause 2.2.2.5, not the warehouse widths
-- from Clause 3.2 and not what SF=5 happens to contain.  Two of them differ
-- from the target and the difference matters:
--
--     C_L_NAME is CHAR(25) here, DimCustomer.LastName is CHAR(30)
--     C_F_NAME is CHAR(20) here, DimCustomer.FirstName is CHAR(30)
--
-- Sizing from the data would have given 17 and 14 at SF=5 and silently
-- truncated at a larger one.
--
-- EVERYTHING IS TEXT, AND THAT IS DELIBERATE
--
-- Clause 4.5.4.2 needs two states a typed column cannot hold at once:
--
--     NULL  the field was absent      -> carry the current value forward
--     ''    the field was <Element/>  -> set the target to NULL
--
-- A DATE column cannot store ''.  So dates, numbers and flags stay as
-- VARCHAR until the forward-fill has run, and are cast in the transform.
-- A value that will not cast is a parsing bug, and finding it at the cast is
-- more informative than finding it at the load.
--
-- In tpcdi_stage, not tpcdi, so the warehouse keeps exactly the twenty tables
-- of Clause 3.2 -- which the Phase 3 checks assert.

CREATE DATABASE IF NOT EXISTS tpcdi_stage;

-- One row per Action, in document order.  Clause 4.5.4.1: every Action has a
-- Customer, including the ones that change no customer field (ADDACCT,
-- UPDACCT, CLOSEACCT carry only C_ID).
DROP TABLE IF EXISTS tpcdi_stage.stg_cmg_customer;
CREATE TABLE tpcdi_stage.stg_cmg_customer (
    -- seq is the position in the document.  It is the only record of the
    -- order the actions must be replayed in: once it is a column, "apply in
    -- order" is an ORDER BY inside a window rather than a loop.
    seq                   INT UNSIGNED NOT NULL,
    ActionType            VARCHAR(9)   NOT NULL,   -- CLOSEACCT is the longest
    ActionTS              DATETIME     NOT NULL,
    C_ID                  VARCHAR(11),             -- IDENT_T = NUM(11)
    C_TAX_ID              VARCHAR(20),
    C_GNDR                VARCHAR(1),
    C_TIER                VARCHAR(1),              -- NUM(1); invalid values
                                                   -- are reported, not rejected
                                                   -- (Clause 4.5.4.4)
    C_DOB                 VARCHAR(10),             -- DATE as YYYY-MM-DD
    C_L_NAME              VARCHAR(25),
    C_F_NAME              VARCHAR(20),
    C_M_NAME              VARCHAR(1),
    C_ADLINE1             VARCHAR(80),
    C_ADLINE2             VARCHAR(80),
    C_ZIPCODE             VARCHAR(12),
    C_CITY                VARCHAR(25),
    C_STATE_PROV          VARCHAR(20),
    C_CTRY                VARCHAR(24),
    C_PRIM_EMAIL          VARCHAR(50),
    C_ALT_EMAIL           VARCHAR(50),
    -- Clause 2.2.2.5.3.  Kept in parts: Clause 4.5.4.3 assembles Phone1..3
    -- from them with four conditional cases, which is a transformation rule.
    C_PHONE_1_C_CTRY_CODE VARCHAR(3),
    C_PHONE_1_C_AREA_CODE VARCHAR(3),
    C_PHONE_1_C_LOCAL     VARCHAR(10),
    C_PHONE_1_C_EXT       VARCHAR(5),
    C_PHONE_2_C_CTRY_CODE VARCHAR(3),
    C_PHONE_2_C_AREA_CODE VARCHAR(3),
    C_PHONE_2_C_LOCAL     VARCHAR(10),
    C_PHONE_2_C_EXT       VARCHAR(5),
    C_PHONE_3_C_CTRY_CODE VARCHAR(3),
    C_PHONE_3_C_AREA_CODE VARCHAR(3),
    C_PHONE_3_C_LOCAL     VARCHAR(10),
    C_PHONE_3_C_EXT       VARCHAR(5),
    C_LCL_TX_ID           VARCHAR(4),
    C_NAT_TX_ID           VARCHAR(4),
    PRIMARY KEY (seq),
    -- The forward-fill partitions by customer and orders by seq.
    KEY ix_cust_seq (C_ID, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per <Account> element.  An action may carry none (UPDCUST, INACT)
-- or several (NEW, ADDACCT), so seq is not unique here.
DROP TABLE IF EXISTS tpcdi_stage.stg_cmg_account;
CREATE TABLE tpcdi_stage.stg_cmg_account (
    seq        INT UNSIGNED NOT NULL,
    ActionType VARCHAR(9)   NOT NULL,
    ActionTS   DATETIME     NOT NULL,
    C_ID       VARCHAR(11),
    CA_ID      VARCHAR(11),
    CA_TAX_ST  VARCHAR(1),               -- NUM(1)
    CA_B_ID    VARCHAR(11),              -- the managing broker's natural key
    CA_NAME    VARCHAR(50),
    KEY ix_acct_seq (CA_ID, seq),
    KEY ix_acct_cust (C_ID, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The cascade in Clause 4.5.4.3 and 4.4.1.6: UPDCUST gives every one of that
-- customer's current accounts a new row, and INACT makes them all inactive.
-- Those events are derived rather than present in the XML, so they are built
-- here and unioned with the explicit ones before dating.
--
-- This is resolvable without a loop because neither action opens or closes an
-- account -- which accounts exist and are open at a given moment is decided
-- by NEW, ADDACCT and CLOSEACCT alone.
DROP TABLE IF EXISTS tpcdi_stage.stg_cmg_account_event;
CREATE TABLE tpcdi_stage.stg_cmg_account_event (
    seq        INT UNSIGNED NOT NULL,
    ActionType VARCHAR(9)   NOT NULL,
    ActionTS   DATETIME     NOT NULL,
    C_ID       VARCHAR(11),
    CA_ID      VARCHAR(11),
    CA_TAX_ST  VARCHAR(1),
    CA_B_ID    VARCHAR(11),
    CA_NAME    VARCHAR(50),
    -- 1 where the row came from a <Account> element, 0 where it was derived
    -- from a customer-level UPDCUST or INACT.  Kept so the transform can tell
    -- "this field was supplied" from "nothing was said about this field",
    -- which is the same absent-versus-empty problem one level up.
    explicit   TINYINT(1)   NOT NULL,
    KEY ix_ev (CA_ID, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;



select *
from tpcdi_stage.stg_cmg_account_event;


select *
from stg_cmg_customer;
select *
from stg_cmg_account;