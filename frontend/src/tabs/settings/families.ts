/** Database families.
 *
 *  Neptune talks to several Postgres databases that belong to different *programs*, not to
 *  different servers: the three `neptune_*` databases are the app's own, `cato_securities` is
 *  CATO's read-only master. Grouping the UI by family (rather than by the four internal roles)
 *  matches how they are actually provisioned — one server per program, several databases on it.
 *
 *  This is presentational only. The backend stores one fully independent connection per role
 *  (`db_connections` is keyed by role alone), so a family card fans its shared credentials out
 *  across its members' rows. Adding `mercury_*` later is an entry here, not a code change.
 */

export type DbRole = "PORTFOLIO" | "SECURITIES" | "MACRO" | "UNIVERSE";

export interface DbMember {
  role: DbRole;
  /** Key of this database's block in Electron's neptune-config.json. */
  electronKey: string;
  label: string;
  /** Default database name, shown as the input placeholder. */
  defaultDatabase: string;
  /** The portfolio DB is the bootstrap: on the web path the engine is built from the
   *  environment at import time, so a stored row is informational until a restart. In Electron
   *  a save rewrites the config and respawns the sidecar, so there it does take effect. */
  bootstrap?: boolean;
}

export interface DbFamily {
  id: string;
  label: string;
  blurb: string;
  members: DbMember[];
}

export const DB_FAMILIES: DbFamily[] = [
  {
    id: "neptune",
    label: "Neptune databases",
    blurb:
      "Neptune's own databases — the app store, market data, and macro history. They normally " +
      "share one Postgres server; give a database its own server under Advanced if it lives elsewhere.",
    members: [
      {
        role: "PORTFOLIO",
        electronKey: "portfolioDb",
        label: "Portfolio (app)",
        defaultDatabase: "neptune_portfolios",
        bootstrap: true,
      },
      {
        role: "SECURITIES",
        electronKey: "securitiesDb",
        label: "Securities (market data)",
        defaultDatabase: "neptune_securities",
      },
      {
        role: "MACRO",
        electronKey: "macroDb",
        label: "Macro (rates, credit, economic)",
        defaultDatabase: "neptune_macro",
      },
    ],
  },
  {
    id: "cato",
    label: "CATO databases",
    blurb:
      "CATO's securities master, read-only. Neptune projects a universe from it and never " +
      "writes to it. Often a separate server from Neptune's own databases.",
    members: [
      {
        role: "UNIVERSE",
        electronKey: "universeDb",
        label: "Universe (cato_securities)",
        defaultDatabase: "cato_securities",
      },
    ],
  },
];

export const ALL_MEMBERS: DbMember[] = DB_FAMILIES.flatMap((f) => f.members);

/** One database's connection settings, normalized across the two config paths. Electron's
 *  config calls the user field `user` and has no sslmode; the API calls it `username`. */
export interface DbConn {
  host: string;
  port: number;
  database: string;
  username: string;
  /** Write-only. Blank means "keep whatever is stored" — never sent as an empty string. */
  password: string;
  sslmode: string;
}

export const EMPTY_CONN: DbConn = {
  host: "",
  port: 5432,
  database: "",
  username: "",
  password: "",
  sslmode: "",
};
