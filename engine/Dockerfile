# The Java engine, serving the endpoints only it can answer: the strategy
# registry and backtest execution. Build context is the project root
# (docker-compose `context: .`).

# --- 1. Build ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /build

# Wrapper and build scripts first, so dependency resolution caches across
# source-only changes.
COPY gradle/ gradle/
COPY gradlew settings.gradle build.gradle ./
# settings.gradle includes the `server` project (the Spring Boot service), so
# its build file has to be present for Gradle to configure; its sources are
# not, and nothing of it is built here.
COPY server/build.gradle server/
RUN chmod +x gradlew && ./gradlew --no-daemon :dependencies --configuration runtimeClasspath > /dev/null 2>&1 || true

COPY src/ src/
# installDist lays out bin/ + lib/ — the `application` plugin's own launcher,
# so the runtime image needs no Gradle.
RUN ./gradlew --no-daemon :installDist -x test

# --- 2. Runtime ---
FROM eclipse-temurin:21-jre
WORKDIR /app

COPY --from=build /build/build/install/BackTesting/ ./

# Matches the jvmRuntimeArgs in build.gradle: JDK 21 warns (and JDK 22 errors)
# on unrestricted native access without this.
ENV JAVA_OPTS="--enable-native-access=ALL-UNNAMED"

# The Java compute worker: it takes java-compute jobs from the jobs table and
# opens no port.
CMD ["./bin/BackTesting", "worker"]
