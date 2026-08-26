@{
    # --- Target instance (Always Free / Ampere A1) ---
    Region      = "eu-frankfurt-1"
    DisplayName = "main-vps"
    Shape       = "VM.Standard.A1.Flex"
    Ocpus       = 2
    MemoryGB    = 12

    # --- Network ---
    VcnName    = "main-vcn"
    SubnetName = "public subnet-main-vcn"

    # --- Image ---
    OsName    = "Canonical Ubuntu"
    OsVersion = "24.04"

    # --- Loop ---
    RetrySeconds = 300

    # --- Hard Always Free limits for Ampere A1 (per tenancy) ---
    # Source: docs.oracle.com "Always Free Resources":
    #   "the first 1,500 OCPU hours and 9,000 GB hours per month ... For Always Free
    #    tenancies, this is equivalent to 2 OCPUs and 12 GB of memory"
    # 1500/730h ~= 2 OCPU, 9000/730h ~= 12 GB when running 24/7.
    # Exceeding this => the catcher refuses to start (cost protection).
    FreeTierMaxOcpus    = 2
    FreeTierMaxMemoryGB = 12
}
