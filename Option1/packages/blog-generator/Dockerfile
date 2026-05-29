FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src
COPY BlogGenerator/ .
RUN dotnet restore
RUN dotnet publish -c Release -o /app

FROM mcr.microsoft.com/dotnet/aspnet:9.0
WORKDIR /app
COPY --from=build /app .
EXPOSE 3002
ENV ASPNETCORE_URLS=http://+:3002
ENTRYPOINT ["dotnet", "BlogGenerator.dll"]
