# DIGen, the TPC-DI source-data generator.
#
# Why its own image: DIGen is a 2014 Java program shipped with PDGF, javassist
# and log4j 1.2.15.  Its README claims "Java SE 7 or above"; third parties
# report it breaks above 1.8.  Pinning Java 8 here means the question never
# reaches the host, and the professor's machine needs no legacy JDK -- the same
# reasoning that put dsdgen in a container for TPC-DS.
#
# The tools are NOT redistributed.  Build context is ./tools, which each group
# member populates from their own TPC download.  tools/MANIFEST.md records the
# SHA-256 so everyone can confirm they unpacked the same thing.
FROM eclipse-temurin:8-jre

COPY Tools/ /opt/tpcdi/

# DIGen spawns its generation JVM with the working directory hard-coded as
# "pdgf" in lower case, but the package ships the directory as "PDGF".  On
# macOS and Windows the filesystem is case-insensitive so both resolve and
# nobody noticed; on Linux the child process dies with
#   Cannot run program ... (in directory "/opt/tpcdi/pdgf"): error=2
# A symlink satisfies the lower-case lookup without renaming what TPC
# shipped, so any reference to "PDGF" still resolves too.
RUN ln -s PDGF /opt/tpcdi/pdgf

WORKDIR /opt/tpcdi
VOLUME /staging

# No CMD args: the caller supplies -sf and -o so the scale factor is always
# explicit in the command line that produced a given dataset.
ENTRYPOINT ["java", "-jar", "DIGen.jar"]
