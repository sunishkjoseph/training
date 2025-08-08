graph LR
  %% Layout
  classDef box fill:#f7f7f9,stroke:#bbb,rx:6,ry:6,color:#111;
  classDef svc fill:#eef7ff,stroke:#5b9bd5,rx:6,ry:6,color:#0b2e4f;
  classDef store fill:#fff6e6,stroke:#e39d3c,rx:6,ry:6,color:#5a3b00;
  classDef compute fill:#f0fff4,stroke:#3aa76d,rx:6,ry:6,color:#0c4128;
  classDef ai fill:#f4f0ff,stroke:#7d5bd5,rx:6,ry:6,color:#2f146d;

  subgraph Sources
    WLVM[WebLogic Servers\n(VM/Bare‑metal)\n*.log, *.out]:::box
    OKE[WebLogic on OKE\n(Pods / containers)\ncontainer logs]:::box
  end

  subgraph Collection
    FBVM[Fluent Bit (Tail+Parse+Lua)\nOn VM]:::svc
    FBD[Fluent Bit DaemonSet\nOn OKE]:::svc
  end

  subgraph Storage
    OS[(OCI Object Storage)\nJSONL logs + health]:::store
  end

  subgraph Eventing
    EVT[OCI Events\nObjectCreated]:::svc
  end

  subgraph Functions
    ING[ingest_json\n(parse→ATP)]:::compute
    SCAN[scan_signals\n(anomaly detect)]:::compute
    RCA[run_rca_agent\n(call GenAI Agent)]:::compute
    APPROVE[request_approval\n(ONS/ODA/Email)]:::compute
    REMED[remediate\n(Instance Agent / WLST)]:::compute
  end

  subgraph Intelligence
    GAI[OCI Generative AI\nCustom Agent]:::ai
  end

  subgraph Data_Plane
    ATP[(Oracle ATP 23ai)\nlogs/incidents/RCA]:::store
    APEX[APEX Dashboard\n+ ODA (approvals)]:::svc
    ONS[OCI Notifications]:::svc
  end

  %% Edges
  WLVM -->|tail| FBVM
  OKE  -->|tail| FBD
  FBVM --> OS
  FBD  --> OS

  OS  --> EVT
  EVT --> ING
  ING --> ATP

  SCAN -->|reads| ATP
  SCAN --> RCA
  RCA -->|context→| GAI
  GAI -->|RCA JSON| RCA
  RCA -->|write| ATP

  RCA --> APPROVE
  APPROVE --> ONS
  ONS --> APEX

  APEX -->|APPROVED/REJECTED| REMED
  REMED -->|Run Command\nWLST/OS| WLVM
  REMED -->|kubectl/exec (opt)| OKE
  REMED --> ATP
sequenceDiagram
  autonumber
  participant WL as WebLogic (VM/OKE)
  participant FB as Fluent Bit
  participant OS as Object Storage
  participant EV as OCI Events
  participant FN as OCI Functions
  participant DB as ATP 23ai
  participant GA as GenAI Custom Agent
  participant UX as APEX/ODA
  participant RC as Remediation (Run Cmd)

  WL->>FB: Emit logs + health
  FB->>OS: Upload JSONL files
  OS-->>EV: ObjectCreated event
  EV->>FN: Invoke ingest_json
  FN->>DB: Parse JSONL → INSERT RCA_LOGS

  rect rgb(245,245,255)
    Note over FN,DB: Scheduled
    FN->>FN: scan_signals (bursts / non‑RUNNING)
    FN->>DB: Create rca_incident rows
  end

  FN->>GA: run_rca_agent(context: recent logs + health)
  GA-->>FN: RCA JSON (summary, causes, checks, actions)
  FN->>DB: Store RCA in rca_incident.suggestion

  FN->>UX: request_approval (via ONS/ODA/APEX)
  UX-->>FN: APPROVED / REJECTED

  alt Approved
    FN->>RC: remediate(action: WLST / Run Command)
    RC->>WL: Execute fix scripts
    RC->>DB: Update incident → RESOLVED
  else Rejected
    FN->>DB: Incident stays OPEN
  end
