# The Spring Boot service (G16; Architecture §6): the backend the client will
# call. Build context is the project root (docker-compose `context: .`).

# --- 1. Build ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /build

# Wrapper and build scripts first, so dependency resolution caches across
# source-only changes. The root build file is the engine's: settings.gradle
# names it as the root project, so it has to be there to configure, and none
# of it is built here.
COPY gradle/ gradle/
COPY gradlew settings.gradle build.gradle ./
COPY server/build.gradle server/
RUN chmod +x gradlew && ./gradlew --no-daemon :server:dependencies --configuration runtimeClasspath > /dev/null 2>&1 || true

COPY server/src/ server/src/
RUN ./gradlew --no-daemon :server:bootJar -x test

# --- 2. Runtime ---
FROM eclipse-temurin:21-jre
WORKDIR /app

COPY --from=build /build/server/build/libs/backtest-server.jar ./

# The Markdown the Docs tab shows, at the paths docs/DocRegistry names them by.
# In compose these are bind-mounted over, so an edit shows without a rebuild.
COPY README.md docs/README.md
COPY docs/readonly-doc/backTesting-10-Getting-Started.md docs/docs/readonly-doc/backTesting-10-Getting-Started.md
COPY docs/readonly-doc/backTesting-01-Requirements.md docs/docs/readonly-doc/backTesting-01-Requirements.md
COPY docs/readonly-doc/backTesting-02-Features.md docs/docs/readonly-doc/backTesting-02-Features.md
COPY docs/readonly-doc/backTesting-03-Design-Notes.md docs/docs/readonly-doc/backTesting-03-Design-Notes.md
COPY docs/readonly-doc/backTesting-04-Architecture.md docs/docs/readonly-doc/backTesting-04-Architecture.md
ENV DOCS_DIR=/app/docs
ENV MODELS_DIR=/data/models

ENV PORT=8001
EXPOSE 8001
CMD ["java", "-jar", "backtest-server.jar"]
