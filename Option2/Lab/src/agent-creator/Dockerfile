FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY AgentCreator/ .
RUN dotnet restore
RUN dotnet publish -c Release -o /app

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /app .
EXPOSE 8002
ENV ASPNETCORE_URLS=http://+:8002
ENTRYPOINT ["dotnet", "AgentCreator.dll"]
