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

ENV PORT=8004
EXPOSE 8004
CMD ["java", "-jar", "backtest-server.jar"]
